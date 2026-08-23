package app

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/paulnegz/langgraphgo/graph"

	"agentforge/agent/core"
	"agentforge/agent/qa"
)

// Selection, pencil, feature and repair are one flow with four openings. Each
// starts from the saved app summary, decides which files that request actually
// touches, lists and reads only those, writes an update plan, applies it, and
// then checks the app still runs.
//
//	load → scope → plan → apply → verify → END

// The kinds of edit the Studio can ask for.
const (
	KindRepair  = "repair"
	KindFeature = "feature"
	KindSelect  = "select"
	KindPencil  = "pencil"
)

// Request is one edit instruction, already translated out of the wire format.
type Request struct {
	Kind    string
	Prompt  string
	Route   string
	Element map[string]any // what the user clicked, from the preview
	Strokes any            // what the user drew, for the pencil tool
	Console string         // what the browser had already logged
}

// session is the state the edit graph carries.
type session struct {
	run     *core.Run
	req     Request
	summary *Summary

	read    []string // files the scope step chose
	targets []string // files the plan will change
	plan    string
	written []string
	undoID  string
}

// Edit runs one edit and returns the route the Studio should show afterwards.
func Edit(ctx context.Context, run *core.Run, req Request) (string, error) {
	if strings.TrimSpace(req.Prompt) == "" {
		return "", fmt.Errorf("there is nothing to do — the request was empty")
	}
	if run.Project == "" {
		return "", fmt.Errorf("open a project before editing it")
	}
	if _, err := os.Stat(run.Dir); err != nil {
		return "", fmt.Errorf("no such project: %s", run.Project)
	}

	s := &session{run: run, req: req}
	compiled, err := s.compile()
	if err != nil {
		return "", err
	}
	if _, err := compiled.Invoke(ctx, s); err != nil {
		if run.Cancelled() {
			return "", core.ErrCancelled
		}
		return "", unwrapNode(err)
	}
	return s.previewRoute(), nil
}

func (s *session) compile() (*graph.StateRunnable, error) {
	g := graph.NewStateGraph()
	g.AddNode("load", s.load)
	g.AddNode("scope", s.scope)
	g.AddNode("plan", s.planUpdate)
	g.AddNode("apply", s.apply)
	g.AddNode("verify", s.verify)

	g.SetEntryPoint("load")
	g.AddEdge("load", "scope")
	g.AddEdge("scope", "plan")
	g.AddEdge("plan", "apply")
	g.AddEdge("apply", "verify")
	g.AddEdge("verify", graph.END)
	return g.Compile()
}

// load reads the summary the build left behind. Without one it falls back to a
// fresh survey, which is slower but always correct.
func (s *session) load(_ context.Context, state any) (any, error) {
	run := s.run
	if err := run.Check(); err != nil {
		return state, err
	}
	core.Refresh(run)
	run.Step(s.firstStage(), "active")
	run.Progress(s.firstStage(), 6)

	s.summary = Load(run)
	if s.summary == nil {
		run.Info("📖 no saved summary — reading the project directly")
	} else {
		run.Info("📖 " + s.summary.App)
	}

	if file := s.elementFile(); file != "" {
		line := 0
		if v, ok := s.req.Element["line"].(float64); ok {
			line = int(v)
		}
		run.ElementPicked(file, line)
	}
	run.ChatIntent(s.req.Kind, firstLine(s.req.Prompt))
	return state, nil
}

const scopeSystem = `You decide which files an edit request touches.

You are given a summary of the application and the request. Name only files that
appear in the summary or the file list. Do not invent paths. Be narrow: a request
to change one button should not open ten files.

Answer with JSON only:
{"read":["files to read before deciding"],
 "change":["files the edit will most likely change"],
 "route":"the route the user should see afterwards, or empty"}`

// scope picks the files, then lists their directories and reads them.
func (s *session) scope(ctx context.Context, state any) (any, error) {
	run := s.run
	if err := run.Check(); err != nil {
		return state, err
	}
	run.Progress(s.firstStage(), 18)

	var b strings.Builder
	b.WriteString(s.requestBlock())
	if block := s.summary.Block(); block != "" {
		b.WriteString("\n" + block + "\n")
	}
	b.WriteString("\nFILES ON DISK\n")
	b.WriteString(core.StructureBlock(run.Structure))

	var reply struct {
		Read   []string `json:"read"`
		Change []string `json:"change"`
		Route  string   `json:"route"`
	}
	if err := run.LLM.JSON(ctx, core.RolePlanner, scopeSystem, b.String(), &reply); err != nil {
		run.Warn("could not scope the request: " + err.Error())
	}

	s.read = keepReal(run, append(reply.Read, reply.Change...))
	s.targets = keepReal(run, reply.Change)
	if s.req.Route == "" {
		s.req.Route = reply.Route
	}

	// Whatever the model picked, the file behind the clicked element and the
	// page for the current route are always relevant.
	for _, extra := range []string{s.elementFile(), s.summary.FileForRoute(s.req.Route), pageFile(run, s.req.Route)} {
		if extra != "" && run.Shell.Exists(extra) {
			s.read = prepend(s.read, extra)
			s.targets = prepend(s.targets, extra)
		}
	}
	if len(s.read) == 0 {
		return state, fmt.Errorf("could not work out which files this request touches")
	}

	// List each chosen file's directory so the model sees its siblings.
	for _, rel := range s.targets {
		if entries, err := run.Shell.Ls(filepath.ToSlash(filepath.Dir(rel))); err == nil {
			for _, e := range entries {
				if !e.Dir && len(s.read) < 24 {
					s.read = appendUnique(s.read, e.Path)
				}
			}
		}
	}
	run.Info(fmt.Sprintf("🔎 reading %d file(s), changing %d", len(s.read), len(s.targets)))
	return state, nil
}

const planSystem = `You write the update plan for one edit.

You are given the request and the current contents of the files it touches. Say
exactly what changes in each file, in terms of behaviour a person could see.
Keep it to what was asked — no refactors, no extra features, no renames the
request did not call for.

Answer with plain text: one short paragraph, then one line per file as
"path — what changes".`

// planUpdate writes the plan and shows it to the user before anything changes.
func (s *session) planUpdate(ctx context.Context, state any) (any, error) {
	run := s.run
	if err := run.Check(); err != nil {
		return state, err
	}
	run.Progress(s.planStage(), 34)

	prompt := s.requestBlock() + "\nTHE FILES\n" + core.ReadFiles(run, s.read, 50000)
	plan, err := run.LLM.Text(ctx, core.RolePlanner, planSystem, prompt)
	if err != nil {
		return state, fmt.Errorf("the update plan failed: %w", err)
	}
	s.plan = strings.TrimSpace(plan)
	run.FeaturePlan(map[string]any{"kind": s.req.Kind, "plan": s.plan, "files": s.targets})
	for _, line := range strings.Split(s.plan, "\n") {
		if line = strings.TrimSpace(line); line != "" {
			run.Info("   " + line)
		}
	}
	return state, nil
}

const applySystem = `You apply one update plan to a Next.js App Router
application.

You are given the plan and the current contents of every file involved. Make
exactly the changes the plan describes and nothing else. Preserve everything the
files already do — imports, exports, handlers, styling — unless the plan says to
change it.

Emit the COMPLETE file for everything you change. Never emit a fragment or a
"rest unchanged" comment.

Output format — nothing else:

<<<FILE path/to/file
...the complete file...
>>>END`

// apply saves an undo point, then rewrites the files.
func (s *session) apply(ctx context.Context, state any) (any, error) {
	run := s.run
	if err := run.Check(); err != nil {
		return state, err
	}
	run.Progress(s.applyStage(), 52)
	s.saveUndoPoint()

	prompt := "UPDATE PLAN\n" + s.plan + "\n\n" + s.requestBlock() +
		"\nTHE FILES\n" + core.ReadFiles(run, s.read, 60000)

	writer := core.NewFileWriter(run)
	if _, err := run.LLM.Stream(ctx, core.RoleBuilder, applySystem, prompt, writer.Feed); err != nil {
		return state, fmt.Errorf("the update failed: %w", err)
	}
	writer.Finish()
	s.written = writer.Written()
	if len(s.written) == 0 {
		return state, fmt.Errorf("the model changed nothing")
	}
	run.Info(fmt.Sprintf("✅ %d file(s) updated", len(s.written)))
	return state, nil
}

// verify boots the app and repairs whatever the change broke, then runs the
// unit suite if the project has one.
func (s *session) verify(ctx context.Context, state any) (any, error) {
	run := s.run
	if err := run.Check(); err != nil {
		return state, err
	}
	core.Refresh(run)
	run.Progress(s.checkStage(), 74)
	run.Info("🔬 Checking the app still runs")

	suite := qa.NewSuite()
	defer suite.Close()

	if err := suite.Dev(ctx, run); err != nil {
		// The change broke the app and could not be repaired: say so and leave
		// the undo point, rather than pretending the edit succeeded.
		return state, fmt.Errorf("%w — undo is available in the Studio", err)
	}
	if len(run.Structure.Tests) > 0 {
		run.Progress(s.checkStage(), 88)
		if err := suite.Unit(ctx, run); err != nil {
			run.Warn("the unit suite did not finish: " + err.Error())
		}
	}
	suite.Save(run)

	// The description of the app has changed, so the note it is read from must too.
	if err := Summarize(ctx, run); err != nil {
		run.Warn("the summary could not be refreshed: " + err.Error())
	}
	run.Progress(s.checkStage(), 100)
	return state, nil
}

// --- undo ---------------------------------------------------------------------

// saveUndoPoint copies the files about to change, so the Studio's undo works.
func (s *session) saveUndoPoint() {
	run := s.run
	id := fmt.Sprintf("u%d", time.Now().UnixNano())
	dir := filepath.Join(run.Paths.Meta(run.Project), "undo", id)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		run.Warn("no undo point could be saved: " + err.Error())
		return
	}
	var saved []string
	for _, rel := range s.targets {
		body, _, err := run.Shell.Read(rel)
		if err != nil {
			continue
		}
		if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("%d.bak", len(saved))), []byte(body), 0o644); err != nil {
			continue
		}
		saved = append(saved, rel)
	}
	if len(saved) == 0 {
		_ = os.RemoveAll(dir)
		return
	}
	_ = core.WriteJSON(filepath.Join(dir, "manifest.json"), map[string]any{"files": saved})
	s.undoID = id
	run.UndoPoint(id, saved)
}

// --- prompt building ----------------------------------------------------------

// requestBlock states the request in the terms the tool that raised it uses.
func (s *session) requestBlock() string {
	var b strings.Builder
	switch s.req.Kind {
	case KindSelect:
		b.WriteString("THE USER CLICKED AN ELEMENT IN THE LIVE PREVIEW AND ASKED FOR A CHANGE\n")
	case KindPencil:
		b.WriteString("THE USER DREW OVER A REGION OF THE LIVE PREVIEW AND ASKED FOR A REDESIGN\n")
	case KindFeature:
		b.WriteString("THE USER ASKED FOR A NEW FEATURE\n")
	default:
		b.WriteString("THE USER REPORTED A PROBLEM\n")
	}
	fmt.Fprintf(&b, "REQUEST: %s\n", strings.TrimSpace(s.req.Prompt))
	if s.req.Route != "" {
		fmt.Fprintf(&b, "ON ROUTE: %s\n", s.req.Route)
	}
	if len(s.req.Element) > 0 {
		fmt.Fprintf(&b, "THE ELEMENT: %s\n", describeElement(s.req.Element))
	}
	if s.req.Kind == KindPencil && s.req.Strokes != nil {
		b.WriteString("The drawing marks the area to change. Redesign that area, " +
			"not the whole page.\n")
	}
	if console := strings.TrimSpace(s.req.Console); console != "" {
		fmt.Fprintf(&b, "\nWHAT THE BROWSER LOGGED\n%s\n", truncate(console, 4000))
	}
	return b.String()
}

// describeElement renders what the preview picker captured.
func describeElement(el map[string]any) string {
	var parts []string
	for _, key := range []string{"tag", "id", "className", "text", "selector", "file", "line"} {
		if v, ok := el[key]; ok && fmt.Sprint(v) != "" && fmt.Sprint(v) != "0" {
			parts = append(parts, key+"="+truncate(fmt.Sprint(v), 200))
		}
	}
	return strings.Join(parts, " ")
}

func (s *session) elementFile() string {
	if s.req.Element == nil {
		return ""
	}
	if f, ok := s.req.Element["file"].(string); ok {
		return strings.TrimPrefix(strings.TrimSpace(f), "/")
	}
	return ""
}

// previewRoute is where the Studio should look after the edit.
func (s *session) previewRoute() string {
	if s.req.Route != "" {
		return s.req.Route
	}
	return "/"
}

// --- stage names --------------------------------------------------------------
//
// studio/lib/work-stages.js draws a different rail per kind, so the step names
// have to match the kind that started the run.

func (s *session) firstStage() string {
	switch s.req.Kind {
	case KindSelect, KindPencil:
		return "find"
	case KindRepair:
		return "reproduce"
	default:
		return "plan"
	}
}

func (s *session) planStage() string {
	switch s.req.Kind {
	case KindPencil:
		return "read"
	case KindRepair:
		return "reproduce"
	case KindSelect:
		return "find"
	default:
		return "plan"
	}
}

func (s *session) applyStage() string {
	switch s.req.Kind {
	case KindSelect:
		return "change"
	case KindPencil:
		return "redesign"
	case KindRepair:
		return "fix"
	default:
		return "write"
	}
}

func (s *session) checkStage() string { return "check" }

// --- small helpers ------------------------------------------------------------

func keepReal(run *core.Run, paths []string) []string {
	var out []string
	for _, raw := range paths {
		rel := strings.TrimPrefix(filepath.ToSlash(strings.TrimSpace(raw)), "./")
		rel = strings.TrimPrefix(rel, "/")
		if rel == "" || strings.Contains(rel, "..") || !run.Shell.Exists(rel) {
			continue
		}
		out = appendUnique(out, rel)
	}
	return out
}

func appendUnique(list []string, value string) []string {
	for _, v := range list {
		if v == value {
			return list
		}
	}
	return append(list, value)
}

func prepend(list []string, value string) []string {
	for i, v := range list {
		if v == value {
			list = append(list[:i], list[i+1:]...)
			break
		}
	}
	return append([]string{value}, list...)
}

// pageFile maps /orders onto app/orders/page.jsx.
func pageFile(run *core.Run, route string) string {
	route = strings.TrimSuffix(strings.TrimSpace(route), "/")
	for _, ext := range []string{".jsx", ".js"} {
		if candidate := "app" + route + "/page" + ext; run.Shell.Exists(candidate) {
			return candidate
		}
	}
	return ""
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	return truncate(strings.TrimSpace(s), 160)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// unwrapNode strips the graph runner's wrapper from an error.
func unwrapNode(err error) error {
	msg := err.Error()
	if strings.HasPrefix(msg, "error in node ") {
		if i := strings.Index(msg, ": "); i > 0 {
			return fmt.Errorf("%s", msg[i+2:])
		}
	}
	return err
}
