package qa

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"agentforge/agent/core"
)

// The auto-scaling rule is the part of the unit loop most likely to be wrong,
// so it is pinned here: four rounds unless progress buys more, and an early
// stop the moment two rounds in a row close nothing.
func TestRoundBudget(t *testing.T) {
	t.Run("green on the first round", func(t *testing.T) {
		if v := newRounds().observe(0); v != stopGreen {
			t.Fatalf("verdict = %v, want stopGreen", v)
		}
	})

	t.Run("four rounds is the floor", func(t *testing.T) {
		r := newRounds()
		// Failures that never move: round 2 and 3 stall, so it stops at 3.
		if v := r.observe(5); v != repairAndContinue {
			t.Fatalf("round 1: %v", v)
		}
		if v := r.observe(5); v != repairAndContinue {
			t.Fatalf("round 2 should repair once before giving up: %v", v)
		}
		if v := r.observe(5); v != stopStalled {
			t.Fatalf("round 3 should stop after two stalled rounds: %v", v)
		}
	})

	t.Run("uses the whole floor while it converges", func(t *testing.T) {
		r := newRounds()
		for i, failures := range []int{8, 6, 4} {
			if v := r.observe(failures); v != repairAndContinue {
				t.Fatalf("round %d: %v, want repairAndContinue", i+1, v)
			}
		}
		// Round 4 is the floor, and progress extends it rather than stopping.
		if v := r.observe(2); v != repairAndContinue {
			t.Fatalf("round 4 was still closing failures, so it should continue: %v", v)
		}
		if r.budget != baseRounds+1 {
			t.Errorf("budget = %d, want %d", r.budget, baseRounds+1)
		}
		if !r.extended {
			t.Error("the extension should be reported so it can be logged")
		}
	})

	t.Run("never runs past the ceiling", func(t *testing.T) {
		r := newRounds()
		failures := 40
		for i := 0; i < roundCeiling+5; i++ {
			failures-- // always converging, so only the ceiling can stop it
			if v := r.observe(failures); v == stopBudget || v == stopStalled {
				if r.round > roundCeiling {
					t.Fatalf("ran %d rounds, ceiling is %d", r.round, roundCeiling)
				}
				return
			}
		}
		t.Fatalf("the loop never stopped — it reached round %d", r.round)
	})

	t.Run("progress resets a stall", func(t *testing.T) {
		r := newRounds()
		r.observe(5)
		r.observe(5) // stalled once
		if r.stalled != 1 {
			t.Fatalf("stalled = %d, want 1", r.stalled)
		}
		r.observe(3) // closed two
		if r.stalled != 0 {
			t.Errorf("progress should clear the stall counter, got %d", r.stalled)
		}
	})
}

// Counts come from the assertions, never from vitest's summary counters, so a
// stale counter cannot report a green suite.
func TestSummariseCountsAssertions(t *testing.T) {
	report := vitestReport{
		NumTotalTests: 99, NumPassedTests: 99, NumFailedTests: 0, // deliberately wrong
		TestResults: []struct {
			Name             string `json:"name"`
			Status           string `json:"status"`
			AssertionResults []struct {
				Title           string   `json:"title"`
				Status          string   `json:"status"`
				FailureMessages []string `json:"failureMessages"`
			} `json:"assertionResults"`
		}{
			{
				Name: "/abs/path/tests/unit/orders.test.js",
				AssertionResults: []struct {
					Title           string   `json:"title"`
					Status          string   `json:"status"`
					FailureMessages []string `json:"failureMessages"`
				}{
					{Title: "lists orders", Status: "passed"},
					{Title: "creates an order", Status: "failed", FailureMessages: []string{"expected 1 to be 2"}},
					{Title: "deletes an order", Status: "skipped"},
				},
			},
		},
	}
	got := summarise(report)
	if got.Passed != 1 || got.Failed != 1 || got.Skipped != 1 || got.Total != 3 {
		t.Fatalf("counts = %+v", got)
	}
	if len(got.Failures) != 1 {
		t.Fatalf("failures = %v", got.Failures)
	}
	if got.Failures[0].File != "tests/unit/orders.test.js" {
		t.Errorf("the absolute path was not made project-relative: %q", got.Failures[0].File)
	}
}

func TestUnitTargetsAndRanking(t *testing.T) {
	run := runIn(t, map[string]string{
		"lib/db.js":               "export const x = 1",
		"components/Table.jsx":    "export default function T(){}",
		"app/api/orders/route.js": "export async function GET(){}",
		"app/orders/page.jsx":     "export default function P(){}",
		"app/layout.jsx":          "export default function L(){}",
		"tests/unit/x.test.js":    "test('x', () => {})",
		"vitest.config.js":        "export default {}",
	})
	core.Refresh(run)

	targets := unitTargets(run)
	if len(targets) != 4 {
		t.Fatalf("targets = %v", targets)
	}
	if targets[0] != "lib/db.js" {
		t.Errorf("shared code should be tested first, got %v", targets)
	}
	for _, unwanted := range []string{"app/layout.jsx", "tests/unit/x.test.js", "vitest.config.js"} {
		for _, got := range targets {
			if got == unwanted {
				t.Errorf("%s should not be a unit target", unwanted)
			}
		}
	}
}

func TestTestNameFor(t *testing.T) {
	cases := map[string]string{
		"app/orders/[id]/page.jsx": "app-orders-id-page",
		"lib/db.js":                "lib-db",
		"components/Table.jsx":     "components-Table",
	}
	for in, want := range cases {
		if got := testNameFor(in); got != want {
			t.Errorf("testNameFor(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestResponseScore(t *testing.T) {
	if got := responseScore(150); got != 100 {
		t.Errorf("a fast page should score 100, got %d", got)
	}
	if got := responseScore(5000); got != 10 {
		t.Errorf("a very slow page should bottom out at 10, got %d", got)
	}
	fast, slow := responseScore(400), responseScore(2000)
	if fast <= slow {
		t.Errorf("faster must score higher: %d vs %d", fast, slow)
	}
}

func TestSecurityRulesFindRealProblems(t *testing.T) {
	run := runIn(t, map[string]string{
		"lib/keys.js":  `const apiKey = "sk-live-abcdef1234567890"`,
		"app/page.jsx": `export default () => <div dangerouslySetInnerHTML={{__html: raw}} />`,
		"lib/safe.js":  `const uri = process.env.MONGODB_URI`,
		"lib/db.js":    `const uri = process.env.MONGODB_URI || 'mongodb://user:pass@host/db'`,
	})
	core.Refresh(run)

	err := NewSuite().Security(context.Background(), run)
	if err != nil {
		t.Fatal(err)
	}
	// Reach into the report the way the Studio would.
	suite := NewSuite()
	_ = suite.Security(context.Background(), run)
	findings := suite.report.Security.Findings

	byFile := map[string]string{}
	for _, f := range findings {
		byFile[f.File] = f.Title
	}
	if byFile["lib/keys.js"] != "hard-coded secret" {
		t.Errorf("the committed key was not found: %v", findings)
	}
	if byFile["app/page.jsx"] != "dangerouslySetInnerHTML" {
		t.Errorf("the raw HTML sink was not found: %v", findings)
	}
	if _, flagged := byFile["lib/safe.js"]; flagged {
		t.Errorf("reading from the environment is not a finding: %v", findings)
	}
	if _, flagged := byFile["lib/db.js"]; flagged {
		t.Errorf("the scaffold's own fallback URI must not be reported: %v", findings)
	}
}

func TestWalkableRoutesSkipDynamic(t *testing.T) {
	run := runIn(t, map[string]string{
		"app/page.jsx":             "x",
		"app/orders/page.jsx":      "x",
		"app/orders/[id]/page.jsx": "x",
	})
	core.Refresh(run)

	routes := walkableRoutes(run)
	for _, r := range routes {
		if r == "/orders/[id]" {
			t.Fatal("a dynamic route has no id to walk with and must be skipped")
		}
	}
	if len(routes) != 2 {
		t.Errorf("routes = %v", routes)
	}
}

func TestPageFileForRoute(t *testing.T) {
	run := runIn(t, map[string]string{
		"app/page.jsx":        "x",
		"app/orders/page.jsx": "x",
	})
	if got := pageFileFor(run, "/orders"); got != "app/orders/page.jsx" {
		t.Errorf("pageFileFor(/orders) = %q", got)
	}
	if got := pageFileFor(run, "/"); got != "app/page.jsx" {
		t.Errorf("pageFileFor(/) = %q", got)
	}
	if got := pageFileFor(run, "/nope"); got != "" {
		t.Errorf("a missing route should answer empty, got %q", got)
	}
}

func TestErrorLineDetection(t *testing.T) {
	bad := []string{
		"Failed to compile.",
		"Module not found: Can't resolve '@/lib/db'",
		"TypeError: Cannot read properties of undefined",
	}
	for _, line := range bad {
		if !isErrorLine(line) {
			t.Errorf("%q should be treated as a runtime error", line)
		}
	}
	good := []string{
		" ⚠ Fast Refresh had to perform a full reload",
		"warn  - some deprecation notice",
		"✓ Compiled /orders in 320ms",
	}
	for _, line := range good {
		if isErrorLine(line) {
			t.Errorf("%q is not a runtime error", line)
		}
	}
}

func TestFilesInErrorText(t *testing.T) {
	run := runIn(t, map[string]string{
		"app/orders/page.jsx": "x",
		"lib/db.js":           "x",
	})
	text := `Error: something broke
    at ./app/orders/page.jsx:12:3
    imported from "lib/db.js"
    and app/ghost/page.jsx which does not exist`

	got := filesIn(text, run)
	if len(got) != 2 {
		t.Fatalf("filesIn = %v", got)
	}
	for _, f := range got {
		if f == "app/ghost/page.jsx" {
			t.Error("a path that is not on disk must not be offered to the repair")
		}
	}
}

// runIn builds a Run over a temporary project containing the given files.
func runIn(t *testing.T, files map[string]string) *core.Run {
	t.Helper()
	projects := t.TempDir()
	dir := filepath.Join(projects, "demo")
	for rel, body := range files {
		p := filepath.Join(dir, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return core.NewRun(context.Background(), core.NewHub(),
		core.Paths{Base: projects, Projects: projects}, core.NewLLM(), "demo", "build")
}
