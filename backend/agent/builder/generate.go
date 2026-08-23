package builder

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"agentforge/agent/core"
)

// Writing code is two things: putting a known-good skeleton on disk once, and
// then asking the model for one task's files at a time — after it has been
// shown what those files currently contain.

const coderSystem = `You write files for a Next.js 16 App Router application.

Stack, fixed: App Router, JavaScript (never TypeScript), Tailwind utility
classes, MongoDB through the existing lib/db.js helper. Client components need
'use client' as their first line. Server code never imports a client component's
state.

Output format — nothing else, no prose, no markdown fences:

<<<FILE app/orders/page.jsx
...the complete file...
>>>END

Rules:
- Emit the COMPLETE file every time. Never emit a fragment, a diff, or a comment
  saying the rest is unchanged.
- Only emit files this task owns.
- Every route you link to must exist or be one of the files you are emitting.
- Every mutation writes to MongoDB through lib/db.js and the next render shows
  the change. A handler that only logs is not an implementation.
- No TODO, no placeholder, no "coming soon", no disabled control standing in for
  a feature.
- Give anything a test will need to find a stable data-testid.`

// build writes the next unfinished task.
func (p *Pipeline) build(ctx context.Context, state any) (any, error) {
	run := state.(*core.Run)
	if err := run.Check(); err != nil {
		return run, err
	}
	p.refresh(run)

	if err := p.scaffold(run); err != nil {
		return run, err
	}

	index := nextTask(run.Tasks)
	if index < 0 {
		return run, nil
	}
	task := run.Tasks[index]

	run.Step("build", "active")
	run.PhaseUpsert(task.ID, task.Title, "active")
	run.Info("🏗️  " + task.Title)
	run.Progress("build", buildProgress(run.Tasks))

	written, err := p.writeTask(ctx, run, task)
	if err != nil {
		run.PhaseUpsert(task.ID, task.Title, "pending")
		return run, err
	}
	if len(written) == 0 {
		run.Warn("   nothing was written for " + task.ID)
	}

	run.Tasks[index].Done = true
	run.Tasks[index].Files = mergePaths(task.Files, written)
	run.PhaseUpsert(task.ID, task.Title, "done")
	_ = core.WriteJSON(run.Paths.PlanFile(run.Project), map[string]any{"tasks": run.Tasks})
	return run, nil
}

// writeTask prompts for one task's files and streams them onto disk.
func (p *Pipeline) writeTask(ctx context.Context, run *core.Run, task core.Task) ([]string, error) {
	prompt := p.taskPrompt(run, task)
	writer := core.NewFileWriter(run)

	_, err := run.LLM.Stream(ctx, core.RoleBuilder, coderSystem, prompt, writer.Feed)
	if err != nil {
		return writer.Written(), fmt.Errorf("writing %s failed: %w", task.ID, err)
	}
	writer.Finish()
	return writer.Written(), nil
}

// taskPrompt shows the model the contract, the tree, and the current contents
// of the files it is about to change.
func (p *Pipeline) taskPrompt(run *core.Run, task core.Task) string {
	var b strings.Builder

	fmt.Fprintf(&b, "TASK %s: %s\n%s\n\n", task.ID, task.Title, task.Intent)
	if len(task.Files) > 0 {
		fmt.Fprintf(&b, "FILES THIS TASK OWNS\n%s\n\n", strings.Join(task.Files, "\n"))
	}
	if covered := coveredRequirements(run.Handoff, task.Covers); covered != "" {
		b.WriteString("REQUIREMENTS THIS TASK MUST SATISFY\n" + covered + "\n")
	}
	if run.Contract != "" {
		b.WriteString("CONTRACT (for context — implement only this task)\n")
		b.WriteString(truncate(run.Contract, 6000) + "\n\n")
	}

	b.WriteString("PROJECT AS IT IS NOW\n")
	b.WriteString(core.StructureBlock(run.Structure))

	if existing := core.ReadFiles(run, relatedFiles(run, task.Files), 40000); existing != "" {
		b.WriteString("\nCURRENT CONTENTS OF THE FILES THAT MATTER\n")
		b.WriteString(existing)
		b.WriteString("\nRewrite the ones this task changes in full. Leave the rest alone.\n")
	}
	if len(p.gaps) > 0 {
		b.WriteString("\nGAPS THIS ROUND MUST CLOSE\n")
		for _, gap := range p.gaps {
			b.WriteString("- " + gap + "\n")
		}
	}
	return b.String()
}

// coveredRequirements renders just the requirements a task claims.
func coveredRequirements(handoff map[string]any, ids []string) string {
	if len(ids) == 0 {
		return ""
	}
	want := map[string]bool{}
	for _, id := range ids {
		want[strings.TrimSpace(id)] = true
	}
	var b strings.Builder
	for _, r := range requirements(handoff) {
		id, _ := r["id"].(string)
		source, _ := r["source_id"].(string)
		if !want[id] && !want[source] {
			continue
		}
		text, _ := r["text"].(string)
		fmt.Fprintf(&b, "- %s %s\n", firstNonEmpty(source, id), strings.TrimSpace(text))
	}
	return b.String()
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

func nextTask(tasks []core.Task) int {
	for i, t := range tasks {
		if !t.Done {
			return i
		}
	}
	return -1
}

// buildProgress maps finished tasks onto the build stage's share of the rail.
func buildProgress(tasks []core.Task) float64 {
	if len(tasks) == 0 {
		return 10
	}
	done := 0
	for _, t := range tasks {
		if t.Done {
			done++
		}
	}
	return 10 + 34*float64(done)/float64(len(tasks))
}

func mergePaths(a, b []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, list := range [][]string{a, b} {
		for _, v := range list {
			if v != "" && !seen[v] {
				seen[v] = true
				out = append(out, v)
			}
		}
	}
	return out
}

// afterBuild loops until every task is done, then hands over to the coverage
// check.
func (p *Pipeline) afterBuild(_ context.Context, state any) string {
	run := state.(*core.Run)
	if run.Cancelled() {
		return "preview"
	}
	if nextTask(run.Tasks) >= 0 {
		return "build"
	}
	return "coverage"
}

// --- scaffold ----------------------------------------------------------------

// scaffold puts the parts of a Next.js project that are the same every time on
// disk, so the model never spends a task on boilerplate it would get subtly
// wrong. Existing files are never overwritten.
func (p *Pipeline) scaffold(run *core.Run) error {
	if p.scaffolded {
		return nil
	}
	p.scaffolded = true

	slug := run.Project
	if slug == "" {
		slug = "agentforge-app"
	}
	db := "agentforge_" + strings.ReplaceAll(core.SafeName(slug), "-", "_")

	pkg := map[string]any{
		"name": slug, "private": true, "version": "0.1.0",
		"scripts": map[string]string{
			"dev":   fmt.Sprintf("next dev --port %d", core.DevPort),
			"build": "next build", "start": "next start",
			"test": "vitest run", "e2e": "playwright test",
		},
		"dependencies": map[string]string{
			"next": "16.3.0", "react": "19.2.0", "react-dom": "19.2.0",
			"mongodb": "6.21.0", "lucide-react": "^0.441.0",
		},
		"devDependencies": map[string]string{
			"@playwright/test": "^1.56.0", "@vitejs/plugin-react": "^4.3.4",
			"@testing-library/react": "^16.1.0", "@testing-library/jest-dom": "^6.6.3",
			"jsdom": "^25.0.1", "vitest": "^2.1.8",
			"tailwindcss": "^4.3.3", "@tailwindcss/postcss": "^4.3.3",
		},
	}
	pkgJSON, _ := json.MarshalIndent(pkg, "", "  ")

	files := map[string]string{
		"package.json": string(pkgJSON) + "\n",
		"next.config.mjs": `/** @type {import('next').NextConfig} */
const nextConfig = {
  // Strict mode double-invokes effects, which double-inserts seed rows.
  reactStrictMode: false,
  typescript: { ignoreBuildErrors: true },
  allowedDevOrigins: ['127.0.0.1', 'localhost'],
}

export default nextConfig
`,
		"jsconfig.json":        "{\n  \"compilerOptions\": { \"baseUrl\": \".\", \"paths\": { \"@/*\": [\"./*\"] } }\n}\n",
		"postcss.config.mjs":   "export default { plugins: { '@tailwindcss/postcss': {} } }\n",
		"app/globals.css":      "@import \"tailwindcss\";\n",
		"vitest.config.js":     vitestConfig,
		"playwright.config.js": fmt.Sprintf(playwrightConfig, core.DevPort),
		"lib/db.js":            fmt.Sprintf(dbHelper, db),
		"app/layout.jsx": `import './globals.css'

export const metadata = { title: '` + slug + `' }

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-slate-900 antialiased">{children}</body>
    </html>
  )
}
`,
	}

	wrote := 0
	for rel, body := range files {
		if run.Shell.Exists(rel) {
			continue
		}
		if err := run.Shell.Write(rel, body); err != nil {
			return fmt.Errorf("scaffolding %s: %w", rel, err)
		}
		run.File(rel, len(body), body)
		wrote++
	}
	if wrote > 0 {
		run.Info(fmt.Sprintf("🧱 scaffolded %d base file(s)", wrote))
		p.refresh(run)
	}
	return nil
}

const dbHelper = `import { MongoClient, ObjectId } from 'mongodb'

const uri = process.env.MONGODB_URI || 'mongodb://127.0.0.1:27017'
const dbName = process.env.MONGODB_DB || '%s'

// One client per process. Next's dev server reloads modules, so cache it on
// globalThis or every reload opens another pool.
let clientPromise = globalThis.__mongoClient
if (!clientPromise) {
  clientPromise = new MongoClient(uri).connect()
  globalThis.__mongoClient = clientPromise
}

export async function db() {
  const client = await clientPromise
  return client.db(dbName)
}

export async function collection(name) {
  return (await db()).collection(name)
}

/** Turn a string from a URL or a form into an ObjectId, or null if it is not one. */
export function oid(value) {
  return ObjectId.isValid(value) ? new ObjectId(value) : null
}

/** Make a document safe to hand to a client component. */
export function plain(doc) {
  if (Array.isArray(doc)) return doc.map(plain)
  if (!doc || typeof doc !== 'object') return doc
  const out = {}
  for (const [k, v] of Object.entries(doc)) {
    out[k] = v instanceof ObjectId ? v.toString()
      : v instanceof Date ? v.toISOString()
      : (v && typeof v === 'object') ? plain(v) : v
  }
  return out
}

export { ObjectId }
`

const vitestConfig = `import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/unit/**/*.test.{js,jsx}', 'tests/api/**/*.test.{js,jsx}'],
    setupFiles: ['tests/setup.js'],
    // One file at a time. A repair round has to see the same failure the
    // previous round left behind, and parallel workers reorder that.
    fileParallelism: false,
    sequence: { concurrent: false },
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('.', import.meta.url)) },
  },
})
`

const playwrightConfig = `import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['json', { outputFile: '.agentforge/qa/playwright.json' }], ['list']],
  use: {
    baseURL: 'http://127.0.0.1:%d',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
`
