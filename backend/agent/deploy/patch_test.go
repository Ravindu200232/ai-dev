package deploy

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// patched stages a project and applies the compatibility patches to it,
// returning the staging copy and what changed.
func patched(t *testing.T, source, target string) (writer, []Artifact, []map[string]string) {
	t.Helper()
	staged := filepath.Join(t.TempDir(), "staged")
	spec, err := (&Intake{}).Read(context.Background(), source, staged)
	if err != nil {
		t.Fatal(err)
	}
	w := writer{source: source, staged: staged}
	records, changes := applyPatches(w, spec.Services[0], target)
	return w, records, changes
}

func changed(changes []map[string]string, path string) bool {
	for _, change := range changes {
		if change["path"] == path {
			return true
		}
	}
	return false
}

func TestPatchesMakeTheProjectDeployable(t *testing.T) {
	w, records, changes := patched(t, project(t), TargetEC2)

	config, ok := w.read("next.config.mjs")
	if !ok || !strings.Contains(config, `output: "standalone"`) {
		t.Errorf("a project with no config gets one: %q", config)
	}
	health, ok := w.read("app/api/health/route.js")
	if !ok || !strings.Contains(health, "force-dynamic") {
		t.Error("a project with no health route gets one")
	}
	if !changed(changes, "next.config.mjs") || !changed(changes, "app/api/health/route.js") {
		t.Errorf("changes = %+v", changes)
	}
	for _, change := range changes {
		if change["reason"] == "" || change["change"] == "" {
			t.Errorf("a change with no reason: %+v", change)
		}
	}
	for _, record := range records {
		if record.Kind != "source-patch" {
			t.Errorf("kind = %q", record.Kind)
		}
	}
}

func TestATypeScriptProjectGetsATypeScriptHealthRoute(t *testing.T) {
	source := project(t)
	write(t, source, "tsconfig.json", `{"compilerOptions":{"strict":true}}`)
	w, _, _ := patched(t, source, TargetEC2)
	if !w.has("app/api/health/route.ts") || w.has("app/api/health/route.js") {
		t.Error("a project with a tsconfig gets .ts, and only .ts")
	}
}

func TestVercelDoesNotGetStandalone(t *testing.T) {
	w, _, changes := patched(t, project(t), TargetVercel)
	if w.has("next.config.mjs") {
		t.Error("Vercel builds the app its own way; standalone output would confuse it")
	}
	if changed(changes, "next.config.mjs") {
		t.Errorf("changes = %+v", changes)
	}
}

func TestStandaloneIsAddedToAConfigThatHasOne(t *testing.T) {
	cases := map[string]string{
		"const nextConfig = {};\n\nexport default nextConfig;\n":                           `output: "standalone"`,
		"const nextConfig = {\n  reactStrictMode: true,\n};\nexport default nextConfig;\n": `output: "standalone"`,
		"module.exports = {\n  images: {},\n};\n":                                          `output: "standalone"`,
		"export default {\n  poweredByHeader: false,\n};\n":                                `output: "standalone"`,
	}
	for body, want := range cases {
		got, ok := addStandalone(body)
		if !ok || !strings.Contains(got, want) {
			t.Errorf("addStandalone(%q) = %q %v", body, got, ok)
		}
	}

	// Already done, and a shape nothing recognises: both are left alone.
	already := "const nextConfig = { output: 'standalone' };\n"
	if got, ok := addStandalone(already); ok || got != already {
		t.Errorf("a config that already says it = %q %v", got, ok)
	}
	strange := "const config = makeConfig({ output: 1 })\nexport default config\n"
	if got, ok := addStandalone(strange); ok || got != strange {
		t.Errorf("a config nothing recognises is never guessed at: %q %v", got, ok)
	}
}

func TestExistingHealthRouteIsNotReplaced(t *testing.T) {
	source := project(t)
	mine := filepath.Join(source, "app", "api", "health")
	if err := os.MkdirAll(mine, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(mine, "route.ts"),
		[]byte("export async function GET() { return Response.json({ mine: true }) }\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	write(t, source, "tsconfig.json", `{"compilerOptions":{"strict":true}}`)
	w, _, changes := patched(t, source, TargetEC2)
	body, _ := w.read("app/api/health/route.ts")
	if !strings.Contains(body, "mine: true") {
		t.Error("the customer's own health route was overwritten")
	}
	if changed(changes, "app/api/health/route.ts") {
		t.Errorf("changes = %+v", changes)
	}
}

func TestPagesRouterGetsAPagesHealthRoute(t *testing.T) {
	source := t.TempDir()
	write(t, source, "package.json", `{"name":"legacy","dependencies":{"next":"13.0.0"}}`)
	write(t, source, "package-lock.json", `{"lockfileVersion":3}`)
	write(t, source, "pages/index.js", "export default function Home() { return null }")
	w, _, _ := patched(t, source, TargetEC2)
	if body, ok := w.read("pages/api/health.js"); !ok || !strings.Contains(body, "status(200)") {
		t.Errorf("pages health = %q", body)
	}
	if w.has("app/api/health/route.js") {
		t.Error("a pages-router project has no app directory to put a route in")
	}
}

func TestDatabaseConnectsOnFirstUse(t *testing.T) {
	source := t.TempDir()
	write(t, source, "package.json", `{"name":"shop","dependencies":{"next":"14.0.0","mongodb":"6.0.0"}}`)
	write(t, source, "package-lock.json", `{"lockfileVersion":3}`)
	write(t, source, "app/page.tsx", "export default function Home() { return null }")
	write(t, source, "lib/mongodb.js", `import { MongoClient } from 'mongodb'

const uri = process.env.MONGODB_URI
let clientPromise = new MongoClient(uri).connect()

export default clientPromise
`)
	write(t, source, "app/orders/page.tsx", `import clientPromise from '@/lib/mongodb'

export default async function Orders() {
  const db = await clientPromise
  return null
}
`)
	w, _, changes := patched(t, source, TargetEC2)

	lib, _ := w.read("lib/mongodb.js")
	if !strings.Contains(lib, "function connection()") {
		t.Errorf("lib/mongodb.js = %q", lib)
	}
	if strings.Contains(lib, "new MongoClient(uri).connect()\n\nexport default clientPromise") {
		t.Error("the eager connection is still there")
	}
	if !changed(changes, "lib/mongodb.js") {
		t.Errorf("changes = %+v", changes)
	}

	// A page that reads the database must not be prerendered at build time.
	page, _ := w.read("app/orders/page.tsx")
	if !strings.Contains(page, "export const dynamic = 'force-dynamic'") {
		t.Errorf("app/orders/page.tsx = %q", page)
	}
	if strings.Index(page, "force-dynamic") < strings.Index(page, "import clientPromise") {
		t.Error("the declaration was put above the imports")
	}
}

func TestAuthIsBuiltOnFirstRequest(t *testing.T) {
	source := t.TempDir()
	write(t, source, "package.json",
		`{"name":"shop","dependencies":{"next":"14.0.0","better-auth":"1.0.0"}}`)
	write(t, source, "package-lock.json", `{"lockfileVersion":3}`)
	write(t, source, "app/page.tsx", "export default function Home() { return null }")
	write(t, source, "app/api/auth/[...all]/route.ts", `import { toNextJsHandler } from 'better-auth/next-js'
import { auth } from '@/lib/auth'

export const { GET, POST } = toNextJsHandler(auth.handler)
`)
	write(t, source, "lib/auth-client.ts", `import { createAuthClient } from 'better-auth/react'

export const authClient = createAuthClient({
  baseURL: process.env.BETTER_AUTH_URL,
})
`)
	w, _, changes := patched(t, source, TargetEC2)

	route, _ := w.read("app/api/auth/[...all]/route.ts")
	if !strings.Contains(route, "await import('@/lib/auth')") {
		t.Errorf("auth route = %q", route)
	}
	client, _ := w.read("lib/auth-client.ts")
	if !strings.Contains(client, "createAuthClient()") || strings.Contains(client, "BETTER_AUTH_URL") {
		t.Errorf("auth client = %q", client)
	}
	if !changed(changes, "lib/auth-client.ts") {
		t.Errorf("changes = %+v", changes)
	}
}

func TestPatchesAreNotAppliedTwice(t *testing.T) {
	source := t.TempDir()
	write(t, source, "package.json", `{"name":"shop","dependencies":{"next":"14.0.0"}}`)
	write(t, source, "package-lock.json", `{"lockfileVersion":3}`)
	write(t, source, "app/page.tsx", "export default function Home() { return null }")
	write(t, source, "app/orders/page.tsx", `export const dynamic = 'force-dynamic'
import { getCollection } from '@/lib/mongodb'

export default async function Orders() { return null }
`)
	_, _, changes := patched(t, source, TargetEC2)
	if changed(changes, "app/orders/page.tsx") {
		t.Errorf("a page that already says it is dynamic is left alone: %+v", changes)
	}
}

func TestForceDynamicGoesBelowTheDirectives(t *testing.T) {
	got := forceDynamic("'use client'\nimport x from 'y'\n\nexport default function P() {}\n")
	lines := strings.Split(got, "\n")
	if lines[0] != "'use client'" || lines[1] != "import x from 'y'" {
		t.Errorf("got = %q", got)
	}
	if lines[3] != "export const dynamic = 'force-dynamic'" {
		t.Errorf("got = %q", got)
	}
}

func TestArtifactRecordsWhatWasThereBefore(t *testing.T) {
	source := t.TempDir()
	write(t, source, "keep.txt", "the customer's own\n")
	staged := t.TempDir()
	w := writer{source: source, staged: staged}

	replaced, err := w.write("keep.txt", "the agent's\n", "source-patch")
	if err != nil {
		t.Fatal(err)
	}
	if !replaced.OriginalExists || replaced.OriginalSHA256 == "" {
		t.Errorf("a file the agent overwrote must record what it replaced: %+v", replaced)
	}
	if replaced.SHA256 == replaced.OriginalSHA256 {
		t.Error("the hashes are of different content")
	}

	fresh, err := w.write("new.txt", "only the agent's\n", "cicd")
	if err != nil {
		t.Fatal(err)
	}
	if fresh.OriginalExists || fresh.OriginalSHA256 != "" {
		t.Errorf("nothing was there before: %+v", fresh)
	}
	if fresh.Size != int64(len("only the agent's\n")) {
		t.Errorf("size = %d", fresh.Size)
	}

	patch := Diff(source, staged, []Artifact{replaced, fresh})
	if !strings.Contains(patch, "--- a/keep.txt") || !strings.Contains(patch, "+the agent's") {
		t.Errorf("diff = %q", patch)
	}
	if !strings.Contains(patch, "-the customer's own") {
		t.Errorf("the diff does not show what was replaced: %q", patch)
	}
}

func TestGeneratedFilesUseLineFeeds(t *testing.T) {
	staged := t.TempDir()
	w := writer{source: t.TempDir(), staged: staged}
	if _, err := w.write("a.yml", "one\r\ntwo\r\n", "cicd"); err != nil {
		t.Fatal(err)
	}
	body, _ := os.ReadFile(filepath.Join(staged, "a.yml"))
	if strings.Contains(string(body), "\r") {
		t.Error("a workflow with CRLF in it fails on the runner")
	}
}

func write(t *testing.T, root, rel, body string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}
