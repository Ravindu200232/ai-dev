package builder

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"agentforge/agent/core"
	"agentforge/agent/server"
)

func TestHandoffUnwrapping(t *testing.T) {
	// The SRS agent answers with an envelope; a staged copy is the bare body.
	enveloped := map[string]any{
		"handoff": map[string]any{"app_name": "Order Desk", "prompt": "build it"},
	}
	if got := handoffBody(enveloped)["app_name"]; got != "Order Desk" {
		t.Errorf("the envelope was not unwrapped: %v", got)
	}
	if got := promptOf(enveloped); got != "build it" {
		t.Errorf("promptOf on an envelope = %q", got)
	}

	bare := map[string]any{"app_name": "Order Desk", "prompt": "build it"}
	if got := handoffBody(bare)["app_name"]; got != "Order Desk" {
		t.Errorf("a bare handoff was mangled: %v", got)
	}
	if got := promptOf(bare); got != "build it" {
		t.Errorf("promptOf on a bare handoff = %q", got)
	}
}

func TestAppNameIsPathSafe(t *testing.T) {
	cases := map[string]string{
		"Order Desk":    "order-desk",
		"../etc/passwd": "passwd",
		"":              "",
	}
	for in, want := range cases {
		got := appName(map[string]any{"app_name": in})
		if got != want {
			t.Errorf("appName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestCleanPathsRejectsEscapes(t *testing.T) {
	got := cleanPaths([]string{
		"app/page.jsx", "./lib/db.js", "/app/orders/page.jsx",
		"../../etc/passwd", "", "  ", "app/page.jsx",
	})
	want := []string{"app/page.jsx", "lib/db.js", "app/orders/page.jsx"}
	if len(got) != len(want) {
		t.Fatalf("cleanPaths = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("cleanPaths[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}

func TestRequirementLinesTruncate(t *testing.T) {
	handoff := map[string]any{"requirements": []any{
		map[string]any{"id": "FR-1", "text": "list orders", "kind": "feature"},
		map[string]any{"id": "FR-2", "text": "create an order", "kind": "feature"},
		map[string]any{"id": "FR-3", "text": "delete an order", "kind": "feature"},
	}}
	if got := requirementLines(handoff, 2); !contains(got, "FR-1") || !contains(got, "and 1 more") {
		t.Errorf("requirementLines = %q", got)
	}
	if got := requirementLines(map[string]any{}, 10); got != "" {
		t.Errorf("no requirements should render nothing, got %q", got)
	}
}

func TestCoveredRequirementsMatchesEitherID(t *testing.T) {
	handoff := map[string]any{"requirements": []any{
		map[string]any{"id": "R1", "source_id": "FR-7", "text": "list orders"},
		map[string]any{"id": "R2", "source_id": "FR-8", "text": "delete orders"},
	}}
	// The planner may cite either the handoff id or the SRS id.
	if got := coveredRequirements(handoff, []string{"FR-7"}); !contains(got, "list orders") {
		t.Errorf("matching by source id failed: %q", got)
	}
	if got := coveredRequirements(handoff, []string{"R2"}); !contains(got, "delete orders") {
		t.Errorf("matching by handoff id failed: %q", got)
	}
	if got := coveredRequirements(handoff, nil); got != "" {
		t.Errorf("no ids should render nothing, got %q", got)
	}
}

func TestTaskSequencing(t *testing.T) {
	tasks := []core.Task{
		{ID: "t1", Done: true}, {ID: "t2"}, {ID: "t3"},
	}
	if got := nextTask(tasks); got != 1 {
		t.Errorf("nextTask = %d, want 1", got)
	}
	if got := len(doneTasks(tasks)); got != 1 {
		t.Errorf("doneTasks = %d, want 1", got)
	}
	all := []core.Task{{Done: true}, {Done: true}}
	if got := nextTask(all); got != -1 {
		t.Errorf("a finished plan should answer -1, got %d", got)
	}
	// Progress must rise with the plan and never exceed the build's share.
	if a, b := buildProgress(tasks), buildProgress(all); a >= b || b > 44 {
		t.Errorf("buildProgress: partial=%v complete=%v", a, b)
	}
}

func TestAfterBuildRouting(t *testing.T) {
	p := &Pipeline{}
	run := newRun(t, nil)
	run.Tasks = []core.Task{{ID: "t1"}, {ID: "t2", Done: true}}
	if got := p.afterBuild(context.Background(), run); got != "build" {
		t.Errorf("unfinished tasks should loop back to build, got %q", got)
	}
	run.Tasks = []core.Task{{ID: "t1", Done: true}}
	if got := p.afterBuild(context.Background(), run); got != "coverage" {
		t.Errorf("a finished plan should go to coverage, got %q", got)
	}
	run.Cancel()
	if got := p.afterBuild(context.Background(), run); got != "preview" {
		t.Errorf("a cancelled run must land on preview so it closes, got %q", got)
	}
}

func TestAfterCoverageStopsLooping(t *testing.T) {
	run := newRun(t, nil)

	clean := &Pipeline{}
	if got := clean.afterCoverage(context.Background(), run); got != "dev" {
		t.Errorf("no gaps should move on to dev, got %q", got)
	}

	gapped := &Pipeline{gaps: []string{"a page is missing"}, coverageRounds: 1}
	if got := gapped.afterCoverage(context.Background(), run); got != "plan" {
		t.Errorf("gaps should go back to planning, got %q", got)
	}

	// A coverage check that keeps finding the same gaps must give up.
	stuck := &Pipeline{gaps: []string{"a page is missing"}, coverageRounds: maxCoverageRounds + 1}
	if got := stuck.afterCoverage(context.Background(), run); got != "dev" {
		t.Errorf("a non-converging coverage loop should move on, got %q", got)
	}
}

func TestMissingPlanFilesAndRoutes(t *testing.T) {
	run := newRun(t, map[string]string{"app/page.jsx": "x"})
	core.Refresh(run)
	run.Tasks = []core.Task{
		{ID: "t1", Title: "Home", Files: []string{"app/page.jsx"}, Done: true},
		{ID: "t2", Title: "Orders", Files: []string{"app/orders/page.jsx"}, Done: true},
		{ID: "t3", Title: "Later", Files: []string{"app/later/page.jsx"}}, // not done yet
	}
	gaps := missingPlanFiles(run)
	if len(gaps) != 1 || !contains(gaps[0], "app/orders/page.jsx") {
		t.Fatalf("missingPlanFiles = %v", gaps)
	}

	run.Handoff = map[string]any{"pages": []any{
		map[string]any{"route": "/"},
		map[string]any{"route": "/checkout"},
	}}
	routeGaps := missingContractRoutes(run)
	if len(routeGaps) != 1 || !contains(routeGaps[0], "/checkout") {
		t.Fatalf("missingContractRoutes = %v", routeGaps)
	}
}

func TestDedupeKeepsOne(t *testing.T) {
	got := dedupe([]string{"a gap", "A GAP ", "another gap", ""})
	if len(got) != 2 {
		t.Fatalf("dedupe = %v", got)
	}
}

func TestRelatedFilesIncludesSharedCode(t *testing.T) {
	run := newRun(t, map[string]string{
		"app/orders/page.jsx":    "x",
		"app/orders/table.jsx":   "x",
		"lib/db.js":              "x",
		"app/layout.jsx":         "x",
		"package.json":           "{}",
		"app/unrelated/page.jsx": "x",
	})
	got := relatedFiles(run, []string{"app/orders/page.jsx"})

	for _, want := range []string{"app/orders/page.jsx", "lib/db.js", "app/layout.jsx", "app/orders/table.jsx"} {
		if !hasString(got, want) {
			t.Errorf("relatedFiles should include %s: %v", want, got)
		}
	}
	if hasString(got, "app/unrelated/page.jsx") {
		t.Errorf("an unrelated page should not be read: %v", got)
	}
}

func TestScaffoldWritesABootableProject(t *testing.T) {
	run := newRun(t, nil)
	p := &Pipeline{}
	if err := p.scaffold(run); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"package.json", "next.config.mjs", "app/layout.jsx", "app/globals.css",
		"lib/db.js", "vitest.config.js", "playwright.config.js", "jsconfig.json",
	} {
		if !run.Shell.Exists(want) {
			t.Errorf("the scaffold did not write %s", want)
		}
	}

	body, _, err := run.Shell.Read("package.json")
	if err != nil {
		t.Fatal(err)
	}
	var pkg struct {
		Scripts map[string]string `json:"scripts"`
	}
	if err := json.Unmarshal([]byte(body), &pkg); err != nil {
		t.Fatalf("the scaffolded package.json is not valid JSON: %v", err)
	}
	if !contains(pkg.Scripts["dev"], "5173") {
		t.Errorf("dev script = %q, it must serve the port the Studio proxies", pkg.Scripts["dev"])
	}

	// Vitest must not run files in parallel, or a repair round judges stale code.
	cfg, _, _ := run.Shell.Read("vitest.config.js")
	if !contains(cfg, "fileParallelism: false") {
		t.Error("the vitest config should disable file parallelism")
	}
}

func TestScaffoldNeverOverwrites(t *testing.T) {
	run := newRun(t, map[string]string{"app/layout.jsx": "// mine"})
	if err := (&Pipeline{}).scaffold(run); err != nil {
		t.Fatal(err)
	}
	body, _, _ := run.Shell.Read("app/layout.jsx")
	if body != "// mine" {
		t.Errorf("an existing file was overwritten: %q", body)
	}
}

func TestRequestForMapsEveryEditKind(t *testing.T) {
	for wire, want := range map[string]string{
		"element_edit": "select", "pencil_edit": "pencil",
		"feature": "feature", "agent_update": "repair",
	} {
		msg := server.Message{Type: wire, Prompt: "make it bigger", Raw: map[string]any{}}
		if got := requestFor(msg).Kind; got != want {
			t.Errorf("%s mapped to %q, want %q", wire, got, want)
		}
	}
}

func TestUnwrapNode(t *testing.T) {
	wrapped := errString("error in node build: the planner failed")
	if got := unwrapNode(wrapped).Error(); got != "the planner failed" {
		t.Errorf("unwrapNode = %q", got)
	}
	plain := errString("npm install failed")
	if got := unwrapNode(plain).Error(); got != "npm install failed" {
		t.Errorf("a plain error should pass through, got %q", got)
	}
}

// --- helpers -----------------------------------------------------------------

func newRun(t *testing.T, files map[string]string) *core.Run {
	t.Helper()
	projects := t.TempDir()
	dir := filepath.Join(projects, "demo")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
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

func contains(haystack, needle string) bool {
	return len(needle) == 0 || (len(haystack) >= len(needle) && indexOf(haystack, needle) >= 0)
}

func indexOf(haystack, needle string) int {
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return i
		}
	}
	return -1
}

func hasString(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

type errString string

func (e errString) Error() string { return string(e) }
