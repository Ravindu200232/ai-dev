package builder

import (
	"strings"
	"testing"

	"agentforge/agent/core"
)

// A file on disk satisfies an existence check while doing nothing at all, which
// is how a build reports full coverage and then serves a blank page.
func TestEmptyPlanFilesCatchesAFileThatWasNeverWritten(t *testing.T) {
	run := newRun(t, map[string]string{
		"app/page.jsx":        "",
		"app/cart/page.jsx":   "   \n\n  ",
		"app/orders/page.jsx": "export default function Orders() {\n  return <main>Every order, with its status</main>\n}\n",
	})
	core.Refresh(run)
	run.Tasks = []core.Task{
		{ID: "t1", Title: "Home", Files: []string{"app/page.jsx"}, Done: true},
		{ID: "t2", Title: "Cart", Files: []string{"app/cart/page.jsx"}, Done: true},
		{ID: "t3", Title: "Orders", Files: []string{"app/orders/page.jsx"}, Done: true},
	}

	gaps := strings.Join(emptyPlanFiles(run), "\n")
	if !strings.Contains(gaps, "app/page.jsx") {
		t.Error("an empty file is not covered by the task that promised it")
	}
	if !strings.Contains(gaps, "app/cart/page.jsx") {
		t.Error("a file holding only whitespace is still empty")
	}
	if strings.Contains(gaps, "app/orders/page.jsx") {
		t.Errorf("a file that does its job was called a gap:\n%s", gaps)
	}
}

// A task still in progress has not claimed anything yet.
func TestEmptyPlanFilesIgnoresUnfinishedTasks(t *testing.T) {
	run := newRun(t, map[string]string{"app/later/page.jsx": ""})
	core.Refresh(run)
	run.Tasks = []core.Task{{ID: "t1", Title: "Later", Files: []string{"app/later/page.jsx"}}}

	if gaps := emptyPlanFiles(run); len(gaps) != 0 {
		t.Errorf("an unfinished task is not a coverage gap: %v", gaps)
	}
}

// missingPlanFiles already reports a file that is not there; saying it twice
// would send the planner the same work under two descriptions.
func TestEmptyPlanFilesLeavesMissingOnesToMissingPlanFiles(t *testing.T) {
	run := newRun(t, map[string]string{"app/page.jsx": "x"})
	core.Refresh(run)
	run.Tasks = []core.Task{{ID: "t1", Title: "Cart", Files: []string{"app/cart/page.jsx"}, Done: true}}

	if gaps := emptyPlanFiles(run); len(gaps) != 0 {
		t.Errorf("a missing file is not this check's to report: %v", gaps)
	}
	if gaps := missingPlanFiles(run); len(gaps) != 1 {
		t.Errorf("and it is that check's to report: %v", gaps)
	}
}

// The review is only as good as what it is shown, so the files the plan
// promised have to be among them.
func TestCoverageSourcesReadsWhatWasPlannedAndWhatServes(t *testing.T) {
	run := newRun(t, map[string]string{
		"app/page.jsx":            "home",
		"app/api/rooms/route.js":  "rooms api",
		"components/RoomCard.jsx": "card",
		"lib/db.js":               "db",
	})
	core.Refresh(run)
	run.Tasks = []core.Task{
		{ID: "t1", Title: "Card", Files: []string{"components/RoomCard.jsx"}, Done: true},
	}

	got := strings.Join(coverageSources(run), "\n")
	for _, want := range []string{"components/RoomCard.jsx", "app/page.jsx", "app/api/rooms/route.js"} {
		if !strings.Contains(got, want) {
			t.Errorf("%s is not read when judging coverage:\n%s", want, got)
		}
	}
	// One entry each, however many ways a file qualifies.
	if n := strings.Count(got, "app/page.jsx"); n != 1 {
		t.Errorf("app/page.jsx appears %d times", n)
	}
}
