// Package builder turns an approved SRS into a working Next.js application and
// drives it through the QA stages.
package builder

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"agentforge/agent/core"
)

// The plan is the spine of a build: a short, ordered list of concrete tasks,
// each naming the files it owns and the requirements it covers. Every later
// phase reads it, and the coverage check adds to it when the survey finds a
// gap the plan never accounted for.

// intake loads the approved SRS handoff. It prefers the copy already staged in
// the project and falls back to asking the SRS agent for it.
func (p *Pipeline) intake(ctx context.Context, state any) (any, error) {
	run := state.(*core.Run)
	if err := run.Check(); err != nil {
		return run, err
	}
	run.Step("plan", "active")
	run.Progress("intake", 2)

	srsID := strings.TrimSpace(p.msg.SRSID)
	if srsID == "" {
		srsID = p.msg.Str("srs_id")
	}

	handoff, contract, err := p.loadHandoff(ctx, run, srsID)
	if err != nil {
		return run, err
	}
	run.Handoff = handoff
	run.Contract = contract

	name := appName(handoff)
	if run.Project == "" || run.Project == "-" {
		if name == "" {
			name = "agentforge-app"
		}
		run.SetDir(name)
		run.Shell.Emit = func(line string) { run.Command(line) }
	}
	run.ProjectExists(run.Project)
	run.Detected("next", "app-router")

	count := len(requirements(handoff))
	if count > 0 {
		run.Info(fmt.Sprintf("📄 %d requirement(s) from the approved specification", count))
	} else if contract != "" {
		run.Info("📄 building from the approved specification")
	} else {
		run.Info("📄 no specification — building from the request as written")
	}
	return run, nil
}

// loadHandoff returns the structured handoff and the contract prompt.
func (p *Pipeline) loadHandoff(ctx context.Context, run *core.Run, srsID string) (map[string]any, string, error) {
	// The staged copy is authoritative once a build has started, so a resume
	// never re-reads a specification that has since been edited.
	staged := filepath.Join(run.Paths.Meta(run.Project), "srs", "handoff.json")
	var onDisk map[string]any
	if core.ReadJSON(staged, &onDisk) == nil && len(onDisk) > 0 {
		return handoffBody(onDisk), promptOf(onDisk), nil
	}
	if srsID == "" {
		// No specification: the typed request is the whole contract.
		return map[string]any{}, strings.TrimSpace(p.msg.Prompt), nil
	}

	if p.srs == nil {
		return nil, "", fmt.Errorf("the specification service is not available")
	}
	handoff, err := p.srs.LiveHandoff(ctx, srsID)
	if err != nil {
		return nil, "", fmt.Errorf("the specification could not be read: %w", err)
	}
	// Stage it beside the project so a resume never re-reads a specification
	// that has since been edited.
	_ = core.WriteJSON(staged, handoff)
	return handoffBody(handoff), promptOf(handoff), nil
}

// handoffBody unwraps the envelope the SRS agent returns.
func handoffBody(h map[string]any) map[string]any {
	if inner, ok := h["handoff"].(map[string]any); ok {
		return inner
	}
	return h
}

func promptOf(h map[string]any) string {
	if s, ok := h["prompt"].(string); ok {
		return strings.TrimSpace(s)
	}
	if inner, ok := h["handoff"].(map[string]any); ok {
		if s, ok := inner["prompt"].(string); ok {
			return strings.TrimSpace(s)
		}
	}
	return ""
}

func appName(h map[string]any) string {
	for _, key := range []string{"project_name", "app_name"} {
		if s, ok := h[key].(string); ok && strings.TrimSpace(s) != "" {
			return core.SafeName(strings.ToLower(strings.ReplaceAll(strings.TrimSpace(s), " ", "-")))
		}
	}
	return ""
}

// requirements pulls the numbered requirement list out of the handoff.
func requirements(h map[string]any) []map[string]any {
	raw, _ := h["requirements"].([]any)
	out := make([]map[string]any, 0, len(raw))
	for _, item := range raw {
		if m, ok := item.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// requirementLines renders the requirements compactly for a prompt.
func requirementLines(h map[string]any, limit int) string {
	reqs := requirements(h)
	if len(reqs) == 0 {
		return ""
	}
	var b strings.Builder
	for i, r := range reqs {
		if i >= limit {
			fmt.Fprintf(&b, "…and %d more\n", len(reqs)-limit)
			break
		}
		id, _ := r["id"].(string)
		text, _ := r["text"].(string)
		kind, _ := r["kind"].(string)
		if id == "" {
			id = "R" + strconv.Itoa(i+1)
		}
		fmt.Fprintf(&b, "- %s [%s] %s\n", id, kind, strings.TrimSpace(text))
	}
	return b.String()
}

// pageLines renders the routes the specification asked for.
func pageLines(h map[string]any) string {
	raw, _ := h["pages"].([]any)
	var b strings.Builder
	for _, item := range raw {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		route, _ := m["route"].(string)
		name, _ := m["page_name"].(string)
		file, _ := m["file"].(string)
		if route == "" {
			continue
		}
		fmt.Fprintf(&b, "- %s — %s%s\n", route, name, optional(" → ", file))
		_ = file
	}
	return b.String()
}

func optional(prefix, value string) string {
	if strings.TrimSpace(value) == "" {
		return ""
	}
	return prefix + value
}

const plannerSystem = `You are the planner for a Next.js 16 App Router build.

You produce a build plan: a short ordered list of concrete tasks. Each task is
one coherent slice of work a coder can finish and check — a page and its data
access, an API route and its model, an auth boundary. Not "set up the project",
not "polish the UI".

Rules:
- Order matters. Shared code (db access, layout, auth) comes before what uses it.
- Every task names the exact files it will create or change, as project-relative
  paths using the App Router layout: app/<route>/page.jsx, app/api/<name>/route.js,
  lib/<name>.js, components/<Name>.jsx.
- Every requirement id in the contract appears in at least one task's "covers".
- 4 to 14 tasks. Fewer, larger tasks beat many trivial ones.
- The stack is fixed: Next.js App Router, JavaScript (not TypeScript), Tailwind,
  MongoDB through lib/db.js. Do not plan a different stack or a migration.

Answer with JSON only:
{"tasks":[{"id":"t1","title":"short imperative title","intent":"what done looks
like, one or two sentences","files":["app/page.jsx"],"covers":["FR-1"]}]}`

// plan asks the model for the task list, then publishes it as the task table.
func (p *Pipeline) plan(ctx context.Context, state any) (any, error) {
	run := state.(*core.Run)
	if err := run.Check(); err != nil {
		return run, err
	}
	p.refresh(run) // every phase looks at the real tree first
	run.Step("plan", "active")
	run.Progress("plan", 6)

	// A replan keeps the tasks already finished and asks only for the rest.
	done := doneTasks(run.Tasks)
	if len(done) > 0 {
		run.Info(fmt.Sprintf("🧭 replanning — %d task(s) already done", len(done)))
	} else {
		run.Info("🧭 Planning the build")
	}

	var reply struct {
		Tasks []core.Task `json:"tasks"`
	}
	if err := run.LLM.JSON(ctx, core.RolePlanner, plannerSystem, p.planPrompt(run, done), &reply); err != nil {
		return run, fmt.Errorf("the planner failed: %w", err)
	}

	tasks := append([]core.Task{}, done...)
	seen := map[string]bool{}
	for _, t := range done {
		seen[t.ID] = true
	}
	for i, t := range reply.Tasks {
		t.ID = strings.TrimSpace(t.ID)
		if t.ID == "" || seen[t.ID] {
			t.ID = fmt.Sprintf("t%d", len(tasks)+i+1)
		}
		t.Title = strings.TrimSpace(t.Title)
		if t.Title == "" {
			t.Title = "Task " + t.ID
		}
		t.Files = cleanPaths(t.Files)
		seen[t.ID] = true
		tasks = append(tasks, t)
	}
	if len(tasks) == 0 {
		return run, fmt.Errorf("the planner returned no tasks")
	}
	run.Tasks = tasks
	_ = core.WriteJSON(run.Paths.PlanFile(run.Project), map[string]any{
		"tasks": tasks, "planned_at": time.Now().UTC().Format(time.RFC3339),
	})
	run.PublishTasks()
	run.Info(fmt.Sprintf("🧭 %d task(s) planned", len(tasks)-len(done)))
	return run, nil
}

// planPrompt gives the planner the contract, what is already on disk, and what
// is already done — so a replan is additive rather than a fresh start.
func (p *Pipeline) planPrompt(run *core.Run, done []core.Task) string {
	var b strings.Builder

	if run.Contract != "" {
		b.WriteString("APPROVED CONTRACT\n")
		b.WriteString(truncate(run.Contract, 12000))
		b.WriteString("\n\n")
	}
	if lines := requirementLines(run.Handoff, 60); lines != "" {
		b.WriteString("REQUIREMENTS\n")
		b.WriteString(lines)
		b.WriteString("\n")
	}
	if lines := pageLines(run.Handoff); lines != "" {
		b.WriteString("ROUTES THE SPECIFICATION ASKED FOR\n")
		b.WriteString(lines)
		b.WriteString("\n")
	}
	if req := strings.TrimSpace(p.msg.Prompt); req != "" {
		b.WriteString("WHAT THE USER TYPED\n")
		b.WriteString(truncate(req, 2000))
		b.WriteString("\n\n")
	}

	b.WriteString("WHAT IS ON DISK RIGHT NOW\n")
	b.WriteString(core.StructureBlock(run.Structure))
	b.WriteString("\n")

	if len(done) > 0 {
		b.WriteString("ALREADY DONE — do not plan these again\n")
		for _, t := range done {
			fmt.Fprintf(&b, "- %s: %s\n", t.ID, t.Title)
		}
		b.WriteString("\nPlan only the work that is still missing.\n")
	}
	if len(p.gaps) > 0 {
		b.WriteString("\nTHE LAST COVERAGE CHECK FOUND THESE GAPS — cover them\n")
		for _, gap := range p.gaps {
			b.WriteString("- " + gap + "\n")
		}
	}
	return b.String()
}

func doneTasks(tasks []core.Task) []core.Task {
	var out []core.Task
	for _, t := range tasks {
		if t.Done {
			out = append(out, t)
		}
	}
	return out
}

// cleanPaths keeps model-proposed paths inside the project and in App Router form.
func cleanPaths(paths []string) []string {
	out := make([]string, 0, len(paths))
	seen := map[string]bool{}
	for _, raw := range paths {
		clean := strings.TrimPrefix(filepath.ToSlash(strings.TrimSpace(raw)), "./")
		clean = strings.TrimPrefix(clean, "/")
		if clean == "" || strings.Contains(clean, "..") || seen[clean] {
			continue
		}
		seen[clean] = true
		out = append(out, clean)
	}
	return out
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "\n…(truncated)"
}

// --- small HTTP helpers for the SRS sidecar ---------------------------------

func getJSON(ctx context.Context, url string) (map[string]any, error) {
	body, err := get(ctx, url)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func getText(ctx context.Context, url string) (string, error) {
	body, err := get(ctx, url)
	return string(body), err
}

func get(ctx context.Context, url string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s answered %d", url, resp.StatusCode)
	}
	return body, nil
}
