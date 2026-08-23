package app

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"agentforge/agent/core"
)

func TestSummaryBlockRendersWhatAnEditNeeds(t *testing.T) {
	s := &Summary{
		App: "An order desk", Stack: "Next.js + MongoDB",
		Entities: []string{"orders"},
		Routes: []RouteNote{{
			Route: "/orders", File: "app/orders/page.jsx", Purpose: "browse orders",
			Renders: []string{"OrderTable"}, TestIDs: []string{"order-row"},
		}},
		APIs:   []APINote{{Route: "/api/orders", File: "app/api/orders/route.js", Methods: []string{"GET", "POST"}}},
		Shared: []FileNote{{File: "lib/db.js", Purpose: "mongo access"}},
	}
	block := s.Block()
	for _, want := range []string{
		"An order desk", "/orders", "app/orders/page.jsx", "order-row",
		"GET/POST /api/orders", "lib/db.js",
	} {
		if !strings.Contains(block, want) {
			t.Errorf("the summary block is missing %q:\n%s", want, block)
		}
	}
	// A nil summary must render nothing rather than panic — an edit can run
	// against a project whose build never reached the summary stage.
	var missing *Summary
	if got := missing.Block(); got != "" {
		t.Errorf("a nil summary should render empty, got %q", got)
	}
}

func TestFileForRoute(t *testing.T) {
	s := &Summary{Routes: []RouteNote{
		{Route: "/", File: "app/page.jsx"},
		{Route: "/orders/", File: "app/orders/page.jsx"},
	}}
	if got := s.FileForRoute("/orders"); got != "app/orders/page.jsx" {
		t.Errorf("a trailing slash should not change the answer, got %q", got)
	}
	if got := s.FileForRoute("/"); got != "app/page.jsx" {
		t.Errorf("FileForRoute(/) = %q", got)
	}
	if got := s.FileForRoute("/nope"); got != "" {
		t.Errorf("an unknown route should answer empty, got %q", got)
	}
	var missing *Summary
	if got := missing.FileForRoute("/"); got != "" {
		t.Errorf("a nil summary should answer empty, got %q", got)
	}
}

func TestSummarySourcesSkipTestsAndConfig(t *testing.T) {
	run := newRun(t, map[string]string{
		"app/page.jsx":           "x",
		"app/api/o/route.js":     "x",
		"app/layout.jsx":         "x",
		"lib/db.js":              "x",
		"components/Table.jsx":   "x",
		"tests/unit/a.test.js":   "x",
		"vitest.config.js":       "x",
		"app/orders/loading.jsx": "x",
	})
	core.Refresh(run)

	got := summarySources(run)
	for _, want := range []string{"lib/db.js", "components/Table.jsx", "app/page.jsx", "app/api/o/route.js", "app/layout.jsx"} {
		if !has(got, want) {
			t.Errorf("summarySources should include %s: %v", want, got)
		}
	}
	for _, unwanted := range []string{"tests/unit/a.test.js", "vitest.config.js", "app/orders/loading.jsx"} {
		if has(got, unwanted) {
			t.Errorf("summarySources should not include %s: %v", unwanted, got)
		}
	}
	// Shared code comes first so the model reads it before the pages using it.
	if got[0] != "lib/db.js" && got[0] != "components/Table.jsx" {
		t.Errorf("shared code should be read first, got %v", got)
	}
}

func TestKeepRealRefusesEscapesAndGhosts(t *testing.T) {
	run := newRun(t, map[string]string{"app/page.jsx": "x", "lib/db.js": "x"})
	got := keepReal(run, []string{
		"app/page.jsx", "./lib/db.js", "/app/page.jsx",
		"../../etc/passwd", "app/ghost.jsx", "",
	})
	if len(got) != 2 {
		t.Fatalf("keepReal = %v — it must drop escapes and files that are not there", got)
	}
	for _, v := range got {
		if v != "app/page.jsx" && v != "lib/db.js" {
			t.Errorf("unexpected path kept: %q", v)
		}
	}
}

func TestEditRefusesEmptyAndMissing(t *testing.T) {
	run := newRun(t, map[string]string{"app/page.jsx": "x"})

	if _, err := Edit(context.Background(), run, Request{Kind: KindSelect}); err == nil {
		t.Error("an empty request should be refused")
	}
	ghost := core.NewRun(context.Background(), core.NewHub(),
		core.Paths{Base: t.TempDir(), Projects: t.TempDir()}, core.NewLLM(), "nothere", "select")
	if _, err := Edit(context.Background(), ghost, Request{Kind: KindSelect, Prompt: "x"}); err == nil {
		t.Error("editing a project that is not on disk should be refused")
	}
	_ = run
}

// The Studio draws a different progress rail per kind, so the stage names have
// to match studio/lib/work-stages.js exactly.
func TestStageNamesMatchTheStudioRails(t *testing.T) {
	rails := map[string][]string{
		KindSelect:  {"find", "find", "change", "check"},
		KindPencil:  {"find", "read", "redesign", "check"},
		KindFeature: {"plan", "plan", "write", "check"},
		KindRepair:  {"reproduce", "reproduce", "fix", "check"},
	}
	for kind, want := range rails {
		s := &session{req: Request{Kind: kind}}
		got := []string{s.firstStage(), s.planStage(), s.applyStage(), s.checkStage()}
		for i := range want {
			if got[i] != want[i] {
				t.Errorf("%s stage %d = %q, want %q", kind, i, got[i], want[i])
			}
		}
	}
}

func TestDescribeElement(t *testing.T) {
	got := describeElement(map[string]any{
		"tag": "button", "text": "Save", "file": "app/page.jsx", "line": float64(12),
		"empty": "",
	})
	for _, want := range []string{"tag=button", "text=Save", "file=app/page.jsx"} {
		if !strings.Contains(got, want) {
			t.Errorf("describeElement is missing %q: %q", want, got)
		}
	}
	if strings.Contains(got, "empty=") {
		t.Errorf("empty fields should be left out: %q", got)
	}
}

func TestRequestBlockCarriesTheContext(t *testing.T) {
	s := &session{req: Request{
		Kind: KindPencil, Prompt: "make this area calmer", Route: "/orders",
		Element: map[string]any{"tag": "section"}, Strokes: []any{1, 2},
		Console: "TypeError: x is not a function",
	}}
	block := s.requestBlock()
	for _, want := range []string{
		"DREW OVER A REGION", "make this area calmer", "/orders",
		"tag=section", "TypeError", "not the whole page",
	} {
		if !strings.Contains(block, want) {
			t.Errorf("the request block is missing %q:\n%s", want, block)
		}
	}
}

func TestPrependMovesToFront(t *testing.T) {
	got := prepend([]string{"a", "b", "c"}, "c")
	if got[0] != "c" || len(got) != 3 {
		t.Errorf("prepend = %v", got)
	}
	got = prepend([]string{"a"}, "z")
	if got[0] != "z" || len(got) != 2 {
		t.Errorf("prepend of a new value = %v", got)
	}
}

func newRun(t *testing.T, files map[string]string) *core.Run {
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
		core.Paths{Base: projects, Projects: projects}, core.NewLLM(), "demo", "select")
}

func has(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}
