package builder

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"agentforge/agent/core"
)

// The planner is told to put every requirement id in some task's "covers".
// Being told is not the same as having done it, and a requirement past the
// prompt's limit was never shown to it at all. Both arrive here as the same
// thing: an id no task claims. Nothing downstream can recover a feature that
// was never planned — the coder builds the task list it is given, and the
// coverage review audits the same list — so it has to be caught at the plan.

// maxCoverPasses is how many times the planner is asked again about
// requirements no task claims. One is nearly always enough, because the second
// prompt names them outright instead of hoping they were read the first time.
const maxCoverPasses = 2

const coverRestSystem = `You are extending an existing build plan for a Next.js
16 App Router application.

You are given the plan so far and a list of requirements that no task in it
covers. Add the tasks needed to cover them, and nothing else. Do not restate,
renumber or reword the tasks already there.

Rules:
- Each new task names the exact files it creates or changes, as project-relative
  paths: app/<route>/page.jsx, app/api/<name>/route.js, lib/<name>.js,
  components/<Name>.jsx.
- Each new task lists in "covers" the ids it closes, from the list you were given.
- Prefer few, whole tasks. One task may cover several related requirements.
- Reuse what is already on disk. Do not plan a different stack or a migration.

Answer with JSON only:
{"tasks":[{"id":"t9","title":"short imperative title","intent":"what done looks
like","files":["app/orders/page.jsx"],"covers":["FR-7"]}]}`

// requirementID is the id a requirement is known by, falling back to its
// position when the specification gave it none — the same fallback
// requirementLines uses, so the planner sees the id this check looks for.
func requirementID(r map[string]any, index int) string {
	if id, _ := r["id"].(string); strings.TrimSpace(id) != "" {
		return strings.TrimSpace(id)
	}
	return "R" + strconv.Itoa(index+1)
}

// uncoveredRequirements names the requirements no task claims to cover. The
// text travels with the id so a second ask does not depend on the requirement
// having been shown the first time.
func uncoveredRequirements(h map[string]any, tasks []core.Task) []string {
	claimed := map[string]bool{}
	for _, t := range tasks {
		for _, id := range t.Covers {
			if id = strings.TrimSpace(id); id != "" {
				claimed[strings.ToUpper(id)] = true
			}
		}
	}
	var out []string
	for i, r := range requirements(h) {
		id := requirementID(r, i)
		if claimed[strings.ToUpper(id)] {
			continue
		}
		text, _ := r["text"].(string)
		if text = strings.TrimSpace(text); text == "" {
			out = append(out, id)
			continue
		}
		out = append(out, id+" "+text)
	}
	return out
}

// coverEveryRequirement asks the planner again for anything the plan misses,
// and reports what it still misses after that.
func (p *Pipeline) coverEveryRequirement(ctx context.Context, run *core.Run) []string {
	for pass := 0; pass < maxCoverPasses; pass++ {
		missed := uncoveredRequirements(run.Handoff, run.Tasks)
		if len(missed) == 0 {
			return nil
		}
		run.Warn(fmt.Sprintf("🧭 %d requirement(s) no task covers — planning them", len(missed)))
		for _, m := range missed {
			run.Info("   • " + m)
		}

		var reply struct {
			Tasks []core.Task `json:"tasks"`
		}
		if err := run.LLM.JSON(ctx, core.RolePlanner, coverRestSystem,
			coverRestPrompt(run, missed), &reply); err != nil {
			run.Warn("the uncovered requirements could not be planned: " + err.Error())
			return missed
		}
		if len(reply.Tasks) == 0 {
			return missed
		}
		before := len(run.Tasks)
		run.Tasks = appendTasks(run.Tasks, reply.Tasks)
		run.Info(fmt.Sprintf("🧭 %d task(s) added to cover them", len(run.Tasks)-before))
	}
	return uncoveredRequirements(run.Handoff, run.Tasks)
}

func coverRestPrompt(run *core.Run, missed []string) string {
	var b strings.Builder
	b.WriteString("THE PLAN SO FAR\n")
	for _, t := range run.Tasks {
		fmt.Fprintf(&b, "- %s: %s (%s) covers [%s]\n",
			t.ID, t.Title, strings.Join(t.Files, ", "), strings.Join(t.Covers, ", "))
	}
	b.WriteString("\nREQUIREMENTS NO TASK COVERS — plan these\n")
	for _, m := range missed {
		b.WriteString("- " + m + "\n")
	}
	if lines := pageLines(run.Handoff); lines != "" {
		b.WriteString("\nROUTES THE SPECIFICATION ASKED FOR\n")
		b.WriteString(lines)
	}
	b.WriteString("\nWHAT IS ON DISK RIGHT NOW\n")
	b.WriteString(core.StructureBlock(run.Structure))
	return b.String()
}
