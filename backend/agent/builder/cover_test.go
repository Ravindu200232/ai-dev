package builder

import (
	"strings"
	"testing"

	"agentforge/agent/core"
)

func handoffWith(reqs ...map[string]any) map[string]any {
	raw := make([]any, 0, len(reqs))
	for _, r := range reqs {
		raw = append(raw, r)
	}
	return map[string]any{"requirements": raw}
}

var threeRequirements = handoffWith(
	map[string]any{"id": "FR-1", "text": "Guests can book a room", "kind": "functional"},
	map[string]any{"id": "FR-2", "text": "Admins can set prices", "kind": "functional"},
	map[string]any{"id": "FR-3", "text": "Guests can leave a review", "kind": "functional"},
)

func TestUncoveredRequirementsNamesWhatThePlanMisses(t *testing.T) {
	tasks := []core.Task{
		{ID: "t1", Title: "Booking", Covers: []string{"FR-1"}},
		{ID: "t2", Title: "Pricing", Covers: []string{"FR-2"}},
	}
	missed := uncoveredRequirements(threeRequirements, tasks)
	if len(missed) != 1 {
		t.Fatalf("one requirement is unplanned, got %v", missed)
	}
	// The text has to travel with the id: the second ask is the only chance a
	// requirement cut from the first prompt gets to be read at all.
	if !strings.Contains(missed[0], "FR-3") || !strings.Contains(missed[0], "leave a review") {
		t.Errorf("the gap does not say what is missing: %q", missed[0])
	}
}

func TestUncoveredRequirementsIsEmptyWhenAllArePlanned(t *testing.T) {
	tasks := []core.Task{
		{ID: "t1", Covers: []string{"FR-1", "FR-2"}},
		{ID: "t2", Covers: []string{"FR-3"}},
	}
	if missed := uncoveredRequirements(threeRequirements, tasks); len(missed) != 0 {
		t.Errorf("a fully covered plan has no gaps, got %v", missed)
	}
}

// The model writes the ids back by hand, so their case is not to be trusted.
func TestUncoveredRequirementsIgnoresCaseAndPadding(t *testing.T) {
	tasks := []core.Task{{ID: "t1", Covers: []string{" fr-1 ", "Fr-2", "FR-3"}}}
	if missed := uncoveredRequirements(threeRequirements, tasks); len(missed) != 0 {
		t.Errorf("ids differing only in case or spacing still cover, got %v", missed)
	}
}

// A requirement with no id of its own is numbered by position. If this check
// numbered it differently from the prompt the planner reads, every such
// requirement would look uncovered however well it was planned.
func TestUnnamedRequirementsUseTheSameIDThePlannerIsShown(t *testing.T) {
	h := handoffWith(
		map[string]any{"text": "Guests can book a room", "kind": "functional"},
		map[string]any{"text": "Admins can set prices", "kind": "functional"},
	)
	shown := requirementLines(h, plannerRequirementLimit)
	missed := uncoveredRequirements(h, nil)

	for _, want := range []string{"R1", "R2"} {
		if !strings.Contains(shown, want) {
			t.Errorf("the planner is not shown %s:\n%s", want, shown)
		}
		if !strings.Contains(strings.Join(missed, "\n"), want) {
			t.Errorf("the check does not look for %s: %v", want, missed)
		}
	}
	if len(missed) != 2 {
		t.Errorf("both requirements are unplanned, got %v", missed)
	}
}

func TestNoRequirementsIsNotAGap(t *testing.T) {
	if missed := uncoveredRequirements(map[string]any{}, nil); len(missed) != 0 {
		t.Errorf("a build with no stated requirements has nothing uncovered: %v", missed)
	}
}

func TestAppendTasksGivesEveryTaskAnIDOfItsOwn(t *testing.T) {
	into := []core.Task{{ID: "t1", Title: "First"}}
	got := appendTasks(into, []core.Task{
		{ID: "t1", Title: "Clashes with the first"},
		{Title: "Has no id"},
		{ID: "t9", Title: "Keeps its own"},
	})

	if len(got) != 4 {
		t.Fatalf("every incoming task is kept, got %d", len(got))
	}
	seen := map[string]bool{}
	for _, task := range got {
		if task.ID == "" {
			t.Error("a task was left without an id")
		}
		if seen[task.ID] {
			t.Errorf("id %q was handed out twice", task.ID)
		}
		seen[task.ID] = true
		if task.Title == "" {
			t.Errorf("task %q was left without a title", task.ID)
		}
	}
	if !seen["t9"] {
		t.Error("a task with a free id should keep it")
	}
}
