package qa

import (
	"context"
	"os"
	"path/filepath"
	"strings"
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

// Which file has the bug is the whole of a repair, and the messages it has to
// be read out of are written by three different tools on two operating systems.
func TestFilesInFindsTheCulpritOnEveryPlatform(t *testing.T) {
	run := runIn(t, map[string]string{
		"app/orders/page.jsx":        "x",
		"lib/db.js":                  "x",
		"tests/e2e/checkout.spec.js": "x",
	})

	cases := []struct {
		name, text, want string
	}{
		{"next, relative", "Error: broke\n    at ./app/orders/page.jsx:12:3", "app/orders/page.jsx"},
		{"node stack, absolute", "TypeError: undefined\n    at Object.<anonymous> (/srv/shop/lib/db.js:8:11)", "lib/db.js"},
		{"windows, relative", `Error: broke` + "\n" + `    at .\app\orders\page.jsx:12:3`, "app/orders/page.jsx"},
		{"windows, absolute", `TypeError` + "\n" + `   at (C:\Users\ravi\shop\lib\db.js:8:11)`, "lib/db.js"},
		{"windows, playwright", `expect(locator).toBeVisible()` + "\n" + `   at tests\e2e\checkout.spec.js:19:3`,
			"tests/e2e/checkout.spec.js"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := filesIn(c.text, run); !hasPath(got, c.want) {
				t.Errorf("filesIn = %v, want it to name %s", got, c.want)
			}
		})
	}
}

func TestFilesInIgnoresWhatItMayNotEdit(t *testing.T) {
	run := runIn(t, map[string]string{"app/page.jsx": "x", "lib/db.js": "x"})

	// A fault raised inside a dependency or a build artefact names a file this
	// project has by the same name. Blaming the project's copy sends the repair
	// at code that is not broken.
	for _, text := range []string{
		"at /srv/shop/node_modules/next/dist/app/page.jsx:1:1",
		"at /srv/shop/.next/server/app/page.jsx:1:1",
	} {
		if got := filesIn(text, run); len(got) != 0 {
			t.Errorf("filesIn(%q) = %v, want nothing", text, got)
		}
	}

	// A project whose own address contains one of the source directory names
	// still resolves to the file inside it.
	deep := runIn(t, map[string]string{"app/page.jsx": "x"})
	if got := filesIn("at /home/me/lib/shop/app/page.jsx:3:1", deep); !hasPath(got, "app/page.jsx") {
		t.Errorf("filesIn = %v", got)
	}
}

func hasPath(list []string, want string) bool {
	for _, item := range list {
		if item == want {
			return true
		}
	}
	return false
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

// The report is served as a file a browser has to open, so what it produces has
// to be a real PDF, not something that merely looks like one.
func TestReportPDFIsValid(t *testing.T) {
	projects := t.TempDir()
	paths := core.Paths{Base: projects, Projects: projects}
	project := "demo"
	if err := os.MkdirAll(paths.Project(project), 0o755); err != nil {
		t.Fatal(err)
	}

	report := Report{
		Project:    project,
		FinishedAt: "2026-08-23T12:00:00Z",
		Runtime:    StageReport{Name: "runtime", Passed: 1, Total: 1},
		Suite:      StageReport{Name: "unit", Passed: 18, Failed: 2, Total: 20, Unresolved: []string{"tests/unit/orders.test.js › creates an order"}},
		API:        StageReport{Name: "api", Passed: 4, Total: 4},
		E2E:        StageReport{Name: "e2e", Passed: 3, Failed: 1, Total: 4, Unresolved: []string{"Place an order"}},
		Security: SecurityReport{Checked: 12, Findings: []Finding{
			{Severity: "high", Title: "hard-coded secret", File: "lib/keys.js", Line: 3,
				Detail: "a credential is written into the source instead of read from the environment"},
		}},
		Performance: &Performance{
			Scores: map[string]int{"performance": 88}, Metrics: map[string]string{"average-response": "240 ms"},
			Routes: []RouteTiming{{Route: "/orders", MS: 240, Bytes: 18000, Status: 200}},
		},
	}
	if err := core.WriteJSON(filepath.Join(paths.Meta(project), "qa", "report.json"), report); err != nil {
		t.Fatal(err)
	}

	pdf, err := PDF(paths, project)
	if err != nil {
		t.Fatal(err)
	}
	text := string(pdf)
	if !strings.HasPrefix(text, "%PDF-1.4") {
		t.Error("the file does not start with a PDF header")
	}
	if !strings.HasSuffix(strings.TrimSpace(text), "%%EOF") {
		t.Error("the file does not end with the PDF end-of-file marker")
	}
	for _, want := range []string{"/Type /Catalog", "/Type /Pages", "/Type /Page", "xref", "trailer", "startxref"} {
		if !strings.Contains(text, want) {
			t.Errorf("the PDF is missing %q", want)
		}
	}
	// Every object must be reachable through the cross-reference table.
	objects := strings.Count(text, " 0 obj")
	entries := strings.Count(text, " 00000 n ")
	if objects != entries {
		t.Errorf("%d objects but %d xref entries", objects, entries)
	}
	for _, want := range []string{"Test report", "demo", "Unit tests", "Security", "hard-coded secret"} {
		if !strings.Contains(text, want) {
			t.Errorf("the report does not mention %q", want)
		}
	}
}

func TestReportPDFRefusesWithoutAReport(t *testing.T) {
	projects := t.TempDir()
	paths := core.Paths{Base: projects, Projects: projects}
	if err := os.MkdirAll(paths.Project("empty"), 0o755); err != nil {
		t.Fatal(err)
	}
	if _, err := PDF(paths, "empty"); err == nil {
		t.Error("a project with no report should be refused, not given a blank PDF")
	}
	if _, err := PDF(paths, "nothere"); err == nil {
		t.Error("a project that does not exist should be refused")
	}
}

// A PDF string literal cannot carry a raw bracket or backslash.
func TestEscapePDF(t *testing.T) {
	got := escapePDF(`a (b) c\d`)
	if got != `a \(b\) c\\d` {
		t.Errorf("escapePDF = %q", got)
	}
	if got := escapePDF("tab\there"); strings.Contains(got, "\t") {
		t.Errorf("a control character survived: %q", got)
	}
	if got := escapePDF("journey ✅ done"); strings.Contains(got, "✅") {
		t.Errorf("a non-WinAnsi rune survived: %q", got)
	}
}

func TestWrap(t *testing.T) {
	got := wrap("the quick brown fox jumps over the lazy dog", 12)
	for _, l := range got {
		if len(l) > 12 {
			t.Errorf("%q is longer than the width", l)
		}
	}
	if len(got) < 3 {
		t.Errorf("wrap = %v", got)
	}
	if wrap("", 10) != nil {
		t.Error("wrapping nothing should give nothing")
	}
}

// A long report has to run onto more pages rather than off the bottom of one.
func TestPaginate(t *testing.T) {
	var lines []line
	for i := 0; i < linesPerPage*2+5; i++ {
		lines = append(lines, line{text: "row", size: bodySize})
	}
	pages := paginate(lines)
	if len(pages) != 3 {
		t.Fatalf("got %d pages, want 3", len(pages))
	}
	for i, page := range pages {
		if len(page) > linesPerPage {
			t.Errorf("page %d holds %d rows, more than %d", i, len(page), linesPerPage)
		}
	}
	if got := paginate(nil); len(got) != 1 {
		t.Errorf("an empty report should still be one page, got %d", len(got))
	}
}

// An edit re-runs the app and the unit tests and nothing else, so it has to
// merge onto the build's report rather than replace it. Saving an empty one
// wiped the API, browser-journey and security results — and the security panel
// then read "0 findings" as a clean bill rather than as an empty state.
func TestSavingAfterAnEditKeepsWhatTheBuildFound(t *testing.T) {
	run := runIn(t, nil)

	build := NewSuite()
	build.report.Project = run.Project
	build.report.API = StageReport{Name: "api", Passed: 7, Total: 7}
	build.report.E2E = StageReport{Name: "e2e", Passed: 4, Total: 4}
	build.report.Suite = StageReport{Name: "unit", Passed: 12, Total: 12}
	build.report.Security = SecurityReport{Checked: 31, Findings: []Finding{
		{Title: "hardcoded API key", Severity: "high"},
	}}
	build.Save(run)

	// What an edit does: a fresh suite, the app and unit tests re-run, save.
	edit := NewSuite()
	edit.Restore(run)
	edit.report.Suite = StageReport{Name: "unit", Passed: 12, Total: 12}
	edit.Save(run)

	var got Report
	if err := core.ReadJSON(filepath.Join(run.Paths.Meta(run.Project), "qa", "report.json"), &got); err != nil {
		t.Fatal(err)
	}
	if got.API.Total != 7 || got.E2E.Total != 4 {
		t.Errorf("the build's checks were erased: api %+v e2e %+v", got.API, got.E2E)
	}
	if got.Security.Checked != 31 || len(got.Security.Findings) != 1 {
		t.Errorf("a real security finding became a clean bill: %+v", got.Security)
	}
}
