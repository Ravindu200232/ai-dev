package builder

import (
	"context"
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"agentforge/agent/core"
)

// Nothing in this pipeline trusts what an earlier phase believed. Before a
// phase decides anything it lists the project and reads what it needs, so the
// plan is always compared against the tree that is actually on disk.

// refresh re-lists the project. Every node calls this first.
func (p *Pipeline) refresh(run *core.Run) { core.Refresh(run) }

// relatedFiles picks what a task or an edit should read: the files it names,
// plus the shared code every page depends on.
func relatedFiles(run *core.Run, named []string) []string {
	seen := map[string]bool{}
	var out []string
	add := func(rel string) {
		if rel == "" || seen[rel] || !run.Shell.Exists(rel) {
			return
		}
		seen[rel] = true
		out = append(out, rel)
	}
	for _, rel := range named {
		add(rel)
	}
	for _, rel := range []string{"lib/db.js", "app/layout.jsx", "app/globals.css", "package.json"} {
		add(rel)
	}
	// A page's siblings tell the model what the app's conventions already are.
	for _, rel := range named {
		if dir := filepath.ToSlash(filepath.Dir(rel)); dir != "." {
			if entries, err := run.Shell.Ls(dir); err == nil {
				for _, e := range entries {
					if !e.Dir {
						add(e.Path)
					}
				}
			}
		}
	}
	return out
}

const coverageSystem = `You audit a Next.js App Router build against its contract.

You are given the contract's requirements and the exact file list from disk.
Report only gaps you can point at: a requirement with no file that could
implement it, a route the specification asked for with no page.jsx, a link or
fetch target that does not exist, an API route the pages call that is missing.

Do not report style, polish or "could be improved". Do not invent files.

Answer with JSON only:
{"gaps":["one sentence naming what is missing and the file that should hold it"],
 "complete":true|false}`

// coverage compares the plan and the contract against the real tree. Gaps
// become new tasks; when there are none the build moves on to the runtime.
func (p *Pipeline) coverage(ctx context.Context, state any) (any, error) {
	run := state.(*core.Run)
	if err := run.Check(); err != nil {
		return run, err
	}
	p.refresh(run)
	run.Step("build", "active")
	run.Progress("coverage", 46)
	run.Info("🔍 Checking coverage against the specification")

	p.gaps = nil

	// The cheap, certain checks first — no model needed to see a missing file.
	p.gaps = append(p.gaps, missingPlanFiles(run)...)
	p.gaps = append(p.gaps, missingContractRoutes(run)...)

	// Then ask the model for what only reading can find.
	if p.coverageRounds < maxCoverageRounds {
		var reply struct {
			Gaps     []string `json:"gaps"`
			Complete bool     `json:"complete"`
		}
		prompt := coveragePrompt(run)
		if err := run.LLM.JSON(ctx, core.RolePlanner, coverageSystem, prompt, &reply); err != nil {
			run.Warn("the coverage review could not run: " + err.Error())
		} else {
			for _, gap := range reply.Gaps {
				if gap = strings.TrimSpace(gap); gap != "" {
					p.gaps = append(p.gaps, gap)
				}
			}
		}
	}

	p.gaps = dedupe(p.gaps)
	if len(p.gaps) == 0 {
		run.Info("✅ every planned file and route is on disk")
		return run, nil
	}
	p.coverageRounds++
	run.Info(fmt.Sprintf("🔧 %d gap(s) to close", len(p.gaps)))
	for _, gap := range p.gaps {
		run.Info("   • " + gap)
	}
	return run, nil
}

// missingPlanFiles names files a finished task promised but never wrote.
func missingPlanFiles(run *core.Run) []string {
	var gaps []string
	for _, t := range run.Tasks {
		if !t.Done {
			continue
		}
		for _, rel := range t.Files {
			if !run.Shell.Exists(rel) {
				gaps = append(gaps, fmt.Sprintf("%s is missing — task %q said it would create it", rel, t.Title))
			}
		}
	}
	return gaps
}

// missingContractRoutes names routes the specification asked for that have no page.
func missingContractRoutes(run *core.Run) []string {
	raw, _ := run.Handoff["pages"].([]any)
	have := map[string]bool{}
	for _, r := range run.Structure.Routes {
		have[r] = true
	}
	var gaps []string
	for _, item := range raw {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		route, _ := m["route"].(string)
		route = strings.TrimSpace(route)
		if route == "" || have[route] {
			continue
		}
		gaps = append(gaps, fmt.Sprintf("the specification asks for %s but no page serves it "+
			"— add app%s/page.jsx", route, strings.TrimSuffix(route, "/")))
	}
	return gaps
}

func coveragePrompt(run *core.Run) string {
	var b strings.Builder
	if lines := requirementLines(run.Handoff, 60); lines != "" {
		b.WriteString("REQUIREMENTS\n" + lines + "\n")
	} else if run.Contract != "" {
		b.WriteString("CONTRACT\n" + truncate(run.Contract, 6000) + "\n\n")
	}
	b.WriteString("THE PLAN\n")
	for _, t := range run.Tasks {
		mark := " "
		if t.Done {
			mark = "x"
		}
		fmt.Fprintf(&b, "[%s] %s — %s (%s)\n", mark, t.ID, t.Title, strings.Join(t.Files, ", "))
	}
	b.WriteString("\nWHAT IS ON DISK\n")
	b.WriteString(core.StructureBlock(run.Structure))
	return b.String()
}

// afterCoverage sends the build back to planning while gaps remain, and gives
// up looping once the checks stop converging.
func (p *Pipeline) afterCoverage(_ context.Context, state any) string {
	run := state.(*core.Run)
	if run.Cancelled() {
		return "preview"
	}
	if len(p.gaps) > 0 && p.coverageRounds <= maxCoverageRounds {
		return "plan"
	}
	if len(p.gaps) > 0 {
		run.Warn(fmt.Sprintf("moving on with %d unresolved gap(s) — the coverage check stopped converging", len(p.gaps)))
	}
	return "dev"
}

func dedupe(items []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range items {
		key := strings.ToLower(strings.TrimSpace(s))
		if key == "" || seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, strings.TrimSpace(s))
	}
	sort.SliceStable(out, func(i, j int) bool { return len(out[i]) < len(out[j]) })
	return out
}
