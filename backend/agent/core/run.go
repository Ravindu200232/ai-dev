// Package core holds the state, the OS tools and the model client that every
// builder and QA node shares. Nothing here reaches into another package.
package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// ErrCancelled is returned by any node that noticed the run was stopped.
var ErrCancelled = errors.New("run cancelled")

// Ports the Studio already expects. studio/next.config.js rewrites
// /__agentforge/api to UIPort, and studio/lib/ws.js dials WSPort.
const (
	DevPort    = 5173
	UIPort     = 7824
	WSPort     = 7825
	SRSPort    = 7826
	DeployPort = 7834
)

// APIPrefix is the path the Studio proxies through.
const APIPrefix = "/__agentforge/api"

// Client is one connected Studio socket. server/ws.go supplies the real one.
type Client interface {
	Send([]byte)
}

// Hub fans run events out to every connected Studio socket.
type Hub struct {
	mu      sync.RWMutex
	clients map[Client]struct{}
}

func NewHub() *Hub {
	return &Hub{clients: map[Client]struct{}{}}
}

func (h *Hub) Add(c Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.clients[c] = struct{}{}
}

func (h *Hub) Remove(c Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.clients, c)
}

func (h *Hub) Count() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.clients)
}

// Emit serialises one event and hands it to every client.
func (h *Hub) Emit(event map[string]any) {
	if h == nil {
		return
	}
	data, err := json.Marshal(event)
	if err != nil {
		return
	}
	h.mu.RLock()
	targets := make([]Client, 0, len(h.clients))
	for c := range h.clients {
		targets = append(targets, c)
	}
	h.mu.RUnlock()
	for _, c := range targets {
		c.Send(data)
	}
}

// Paths locates the backend tree and the generated projects inside it.
type Paths struct {
	Base     string // backend/
	Projects string // backend/production-ready/
	Logs     string // backend/logs/
	Deploy   string // backend/production-ready/.deploy/
}

// DiscoverPaths resolves the tree from the running binary, honouring the same
// AGENTFORGE_PROJECTS override the Python backend used.
func DiscoverPaths() Paths {
	base := os.Getenv("AGENTFORGE_BASE")
	if base == "" {
		if exe, err := os.Executable(); err == nil {
			// The binary is built into backend/agent/bin, so backend/ is two up.
			base = filepath.Dir(filepath.Dir(filepath.Dir(exe)))
		}
	}
	if base == "" {
		base, _ = os.Getwd()
	}
	p := Paths{Base: base, Logs: filepath.Join(base, "logs")}
	p.Projects = filepath.Join(base, "production-ready")
	if raw := strings.TrimSpace(os.Getenv("AGENTFORGE_PROJECTS")); raw != "" {
		dir := raw
		if !filepath.IsAbs(dir) {
			dir = filepath.Join(base, dir)
		}
		if err := os.MkdirAll(dir, 0o755); err == nil {
			p.Projects = dir
		}
	}
	// Deployment state sits beside the projects it deploys, so moving the
	// projects directory moves the runs that belong to them.
	p.Deploy = filepath.Join(p.Projects, ".deploy")
	_ = os.MkdirAll(p.Projects, 0o755)
	_ = os.MkdirAll(p.Logs, 0o755)
	return p
}

// Project is the directory one generated app lives in.
func (p Paths) Project(name string) string {
	return filepath.Join(p.Projects, SafeName(name))
}

// Meta is where the agent keeps its own notes about a project: the app
// summary, the QA report and the task plan.
func (p Paths) Meta(name string) string {
	return filepath.Join(p.Project(name), ".agentforge")
}

func (p Paths) SummaryFile(name string) string {
	return filepath.Join(p.Meta(name), "summary.json")
}

func (p Paths) QAFile(name string) string {
	return filepath.Join(p.Meta(name), "qa.json")
}

func (p Paths) PlanFile(name string) string {
	return filepath.Join(p.Meta(name), "plan.json")
}

func (p Paths) E2ESummaryFile(name string) string {
	return filepath.Join(p.Meta(name), "e2e-summary.json")
}

// SafeName keeps a project name to one path segment.
func SafeName(name string) string {
	name = strings.TrimSpace(name)
	name = strings.ReplaceAll(name, "\\", "/")
	if i := strings.LastIndex(name, "/"); i >= 0 {
		name = name[i+1:]
	}
	name = strings.TrimLeft(name, ".")
	var b strings.Builder
	for _, r := range name {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == '-', r == '_', r == '.':
			b.WriteRune(r)
		default:
			b.WriteRune('-')
		}
	}
	return strings.Trim(b.String(), "-")
}

// Task is one unit of the build plan. The Studio renders these as the task
// table, keyed on ID.
type Task struct {
	ID     string   `json:"id"`
	Title  string   `json:"title"`
	Intent string   `json:"intent"`
	Files  []string `json:"files"`
	Covers []string `json:"covers"`
	Done   bool     `json:"done"`
}

// Structure is what `ls` and the file reads last told us about the project.
// Every phase refreshes it rather than trusting what an earlier phase believed.
type Structure struct {
	Files   []string  `json:"files"`
	Dirs    []string  `json:"dirs"`
	Routes  []string  `json:"routes"`
	APIs    []string  `json:"apis"`
	Tests   []string  `json:"tests"`
	TakenAt time.Time `json:"taken_at"`
}

// Has reports whether the last survey saw this project-relative path.
func (s *Structure) Has(path string) bool {
	if s == nil {
		return false
	}
	path = filepath.ToSlash(strings.TrimPrefix(path, "./"))
	for _, f := range s.Files {
		if f == path {
			return true
		}
	}
	return false
}

// Run is one build, repair or edit. It is the state every graph node receives
// and returns, so nothing has to be shared through globals.
type Run struct {
	Hub   *Hub
	Paths Paths
	LLM   *LLM
	Shell *Shell

	Project string // project directory name
	Dir     string // absolute path to the project
	Kind    string // build | repair | feature | select | pencil | image
	Prompt  string // the instruction that started this run

	Handoff   map[string]any // the SRS builder-handoff
	Contract  string         // the SRS builder-prompt
	Tasks     []Task
	Structure *Structure
	Summary   map[string]any
	QA        map[string]any

	Round  int // QA round counter, for the auto-scaling loops
	Errors []string

	ctx    context.Context
	cancel context.CancelFunc
	done   atomic.Bool
	mu     sync.Mutex
}

// NewRun starts a run and gives it a cancellable context.
func NewRun(parent context.Context, hub *Hub, paths Paths, llm *LLM, project, kind string) *Run {
	ctx, cancel := context.WithCancel(parent)
	r := &Run{
		Hub:     hub,
		Paths:   paths,
		LLM:     llm,
		Project: SafeName(project),
		Kind:    kind,
		ctx:     ctx,
		cancel:  cancel,
		QA:      map[string]any{},
	}
	r.Dir = paths.Project(r.Project)
	r.Shell = NewShell(r.Dir)
	return r
}

func (r *Run) Context() context.Context { return r.ctx }

// Cancel stops the run. Nodes notice at their next Check.
func (r *Run) Cancel() {
	r.done.Store(true)
	r.cancel()
}

// Cancelled reports whether the run was stopped.
func (r *Run) Cancelled() bool {
	if r.done.Load() {
		return true
	}
	select {
	case <-r.ctx.Done():
		return true
	default:
		return false
	}
}

// Check is what every node calls before doing more work.
func (r *Run) Check() error {
	if r.Cancelled() {
		return ErrCancelled
	}
	return nil
}

// SetDir re-points the run once the project name is known.
func (r *Run) SetDir(project string) {
	r.Project = SafeName(project)
	r.Dir = r.Paths.Project(r.Project)
	r.Shell = NewShell(r.Dir)
}

// --- Events -----------------------------------------------------------------
//
// Every shape below is one the Studio already parses in studio/lib/ws.js.
// Adding a field is safe; renaming one is not.

func (r *Run) emit(event map[string]any) {
	r.Hub.Emit(event)
}

func (r *Run) Log(level, text string) {
	r.emit(map[string]any{"type": "log", "level": level, "text": text})
}

func (r *Run) Info(text string)  { r.Log("INFO", text) }
func (r *Run) Warn(text string)  { r.Log("WARN", text) }
func (r *Run) Fault(text string) { r.Log("ERROR", text) }

// Step marks a pipeline stage. status is one of active, done, error.
func (r *Run) Step(id, status string) {
	r.emit(map[string]any{"type": "step", "step": id, "status": status})
}

// Progress reports one forward-only percentage for the named stage.
func (r *Run) Progress(step string, pct float64) {
	r.emit(map[string]any{"type": "progress", "step": step, "pct": pct})
}

// PhaseUpsert adds or updates one row of the task table.
func (r *Run) PhaseUpsert(id, title, status string) {
	r.emit(map[string]any{"type": "phase", "phase": id, "title": title, "status": status})
}

// PublishTasks paints the whole plan at once, so the table appears complete
// before the first task starts.
func (r *Run) PublishTasks() {
	r.mu.Lock()
	tasks := append([]Task(nil), r.Tasks...)
	r.mu.Unlock()
	for _, t := range tasks {
		status := "pending"
		if t.Done {
			status = "done"
		}
		r.PhaseUpsert(t.ID, t.Title, status)
	}
}

func (r *Run) File(name string, size int, content string) {
	r.emit(map[string]any{"type": "file", "name": name, "size": size, "content": content})
}

func (r *Run) StreamStart(file string) {
	r.emit(map[string]any{"type": "stream_start", "file": file})
}

func (r *Run) StreamToken(token string) {
	r.emit(map[string]any{"type": "stream", "token": token})
}

func (r *Run) StreamEnd(file, content string) {
	r.emit(map[string]any{"type": "stream_end", "file": file, "content": content})
}

func (r *Run) TestStart() {
	r.emit(map[string]any{"type": "test_start"})
}

func (r *Run) TestRun(attempt int) {
	r.emit(map[string]any{"type": "test_run", "attempt": attempt})
}

// TestResult adds one row to the checks table. status is pass, fail or warn.
func (r *Run) TestResult(status, msg, detail string) {
	r.emit(map[string]any{"type": "test_result", "status": status, "msg": msg, "detail": detail})
}

func (r *Run) TestFixing(attempt int, errs []string) {
	r.emit(map[string]any{"type": "test_fixing", "attempt": attempt, "errors": errs})
}

// E2E drives the live browser overlay. state is journey_start, step, step_failed
// or journey_done.
func (r *Run) E2E(event map[string]any) {
	out := map[string]any{"type": "e2e_event"}
	for k, v := range event {
		out[k] = v
	}
	r.emit(out)
}

func (r *Run) Detected(siteType, strategy string) {
	r.emit(map[string]any{"type": "detected", "site_type": siteType, "strategy": strategy})
}

func (r *Run) AgentMsg(text string) {
	r.emit(map[string]any{"type": "agent_msg", "text": text})
}

func (r *Run) ChatIntent(intent, summary string) {
	r.emit(map[string]any{"type": "chat_intent", "intent": intent, "summary": summary})
}

func (r *Run) Command(line string) {
	r.emit(map[string]any{"type": "command", "text": line})
}

func (r *Run) ProjectExists(name string) {
	r.emit(map[string]any{"type": "project", "project": name})
}

func (r *Run) ElementPicked(file string, line int) {
	r.emit(map[string]any{"type": "element_picked", "file": file, "line": line})
}

func (r *Run) UndoPoint(id string, files []string) {
	if files == nil {
		files = []string{}
	}
	r.emit(map[string]any{"type": "undo_point", "id": id, "files": files})
}

func (r *Run) FeaturePlan(plan any) {
	r.emit(map[string]any{"type": "feature_plan", "plan": plan})
}

func (r *Run) DemoAccounts(accounts any) {
	r.emit(map[string]any{"type": "demo_accounts", "accounts": accounts})
}

// Ask pauses the run with a question. The Studio replies by resending the last
// edit with a prompt.
func (r *Run) Ask(kind, file, route string, routes, options []string) {
	if routes == nil {
		routes = []string{}
	}
	if options == nil {
		options = []string{}
	}
	r.emit(map[string]any{
		"type": "ask", "kind": kind, "file": file, "route": route,
		"routes": routes, "options": options,
	})
}

// Done closes the run. preview is the route the Studio should show.
func (r *Run) Done(preview string) {
	if preview == "" {
		preview = "/"
	}
	r.emit(map[string]any{
		"type":    "done",
		"url":     DevURL(),
		"project": r.Project,
		"preview": preview,
	})
}

func (r *Run) Cancelled_(detail map[string]any) {
	out := map[string]any{"type": "cancelled", "project": r.Project}
	for k, v := range detail {
		out[k] = v
	}
	r.emit(out)
}

func (r *Run) Failed(text string) {
	r.emit(map[string]any{"type": "error", "text": text})
}

// DevURL is where the generated app is served while it is being built.
func DevURL() string {
	return "http://localhost:" + itoa(DevPort)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

// --- Meta files -------------------------------------------------------------

// --- shared prompt and writing tools ----------------------------------------

const surveyBudget = 120 // files named in one prompt before the list is trimmed

// Refresh re-runs the `ls` survey. Every phase calls this before it decides
// anything, so no phase is reasoning about a tree that has since changed.
func Refresh(run *Run) {
	st, err := run.Shell.Survey()
	if err != nil && st == nil {
		return
	}
	run.Structure = st
}

// StructureBlock renders a survey for a prompt: the shape first, then the
// files, capped so a large project cannot crowd out the instruction.
func StructureBlock(st *Structure) string {
	if st == nil || len(st.Files) == 0 {
		return "(the project directory is empty — this is a new build)\n"
	}
	var b strings.Builder
	fmt.Fprintf(&b, "%d file(s) on disk.\n", len(st.Files))
	if len(st.Routes) > 0 {
		fmt.Fprintf(&b, "pages: %s\n", strings.Join(st.Routes, ", "))
	}
	if len(st.APIs) > 0 {
		fmt.Fprintf(&b, "api routes: %s\n", strings.Join(st.APIs, ", "))
	}
	if len(st.Tests) > 0 {
		fmt.Fprintf(&b, "tests: %d file(s)\n", len(st.Tests))
	}
	b.WriteString("files:\n")
	for _, f := range trimForPrompt(st.Files) {
		b.WriteString("  " + f + "\n")
	}
	return b.String()
}

// trimForPrompt keeps the source the agent reasons about and drops the noise.
func trimForPrompt(files []string) []string {
	if len(files) <= surveyBudget {
		return files
	}
	var kept []string
	for _, f := range files {
		if strings.HasPrefix(f, "app/") || strings.HasPrefix(f, "lib/") ||
			strings.HasPrefix(f, "components/") || strings.HasPrefix(f, "tests/") ||
			!strings.Contains(f, "/") {
			kept = append(kept, f)
		}
	}
	if len(kept) > surveyBudget {
		kept = kept[:surveyBudget]
	}
	return kept
}

// ReadFiles returns the named files as a prompt block, skipping what is not
// there. This is the "read the code before changing it" half of the tool use.
func ReadFiles(run *Run, paths []string, budget int) string {
	var b strings.Builder
	spent := 0
	for _, rel := range paths {
		if spent >= budget {
			break
		}
		body, truncated, err := run.Shell.Read(rel)
		if err != nil {
			continue
		}
		if len(body) > budget-spent {
			body = body[:budget-spent]
			truncated = true
		}
		spent += len(body)
		fmt.Fprintf(&b, "\n--- %s ---\n%s\n", rel, body)
		if truncated {
			b.WriteString("…(truncated)\n")
		}
	}
	return b.String()
}

// --- streaming file writer ---------------------------------------------------

// The model announces each file with a marker instead of returning JSON, because
// JSON-escaping a whole source file is where small models lose their footing.
const (
	fileOpen  = "<<<FILE "
	fileClose = ">>>END"
)

// FileWriter turns a model's token stream into files on disk, emitting the
// stream events that make the Studio's code pane follow along live.
type FileWriter struct {
	run     *Run
	buf     strings.Builder
	path    string // the file currently open, "" between files
	body    strings.Builder
	written []string
}

func NewFileWriter(run *Run) *FileWriter { return &FileWriter{run: run} }

// Feed accepts one chunk of model output.
func (w *FileWriter) Feed(chunk string) {
	w.buf.WriteString(chunk)
	for w.step() {
	}
}

// step consumes one complete marker, reporting whether it made progress.
func (w *FileWriter) step() bool {
	text := w.buf.String()

	if w.path == "" {
		start := strings.Index(text, fileOpen)
		if start < 0 {
			w.keepTail(text, len(fileOpen)) // a marker may be split across chunks
			return false
		}
		nl := strings.IndexByte(text[start:], '\n')
		if nl < 0 {
			return false // the path line has not finished arriving
		}
		path := strings.TrimSpace(text[start+len(fileOpen) : start+nl])
		w.replaceBuf(text[start+nl+1:])
		if path == "" {
			return true
		}
		w.path = path
		w.body.Reset()
		w.run.StreamStart(path)
		return true
	}

	end := strings.Index(text, fileClose)
	if end < 0 {
		// Everything except a possible partial terminator is file content.
		safe := len(text) - len(fileClose)
		if safe <= 0 {
			return false
		}
		w.emit(text[:safe])
		w.replaceBuf(text[safe:])
		return false
	}
	w.emit(text[:end])
	w.replaceBuf(text[end+len(fileClose):])
	w.closeFile()
	return true
}

func (w *FileWriter) replaceBuf(rest string) {
	w.buf.Reset()
	w.buf.WriteString(rest)
}

func (w *FileWriter) keepTail(text string, keep int) {
	if len(text) > keep {
		w.replaceBuf(text[len(text)-keep:])
	}
}

func (w *FileWriter) emit(chunk string) {
	if chunk == "" {
		return
	}
	w.body.WriteString(chunk)
	w.run.StreamToken(chunk)
}

// closeFile writes the finished file and tells the Studio it is complete.
func (w *FileWriter) closeFile() {
	path := w.path
	body := strings.TrimRight(strings.TrimLeft(w.body.String(), "\n"), " \t\n") + "\n"
	w.path = ""
	w.body.Reset()

	if err := w.run.Shell.Write(path, body); err != nil {
		w.run.Warn("could not write " + path + ": " + err.Error())
		w.run.StreamEnd(path, "")
		return
	}
	w.written = append(w.written, path)
	w.run.StreamEnd(path, body)
	w.run.File(path, len(body), body)
	w.run.Info("   ✎ " + path)
}

// Finish closes a file the model left open, which small models sometimes do.
func (w *FileWriter) Finish() {
	if w.path == "" {
		return
	}
	w.emit(w.buf.String())
	w.buf.Reset()
	w.closeFile()
}

// Written lists the files this stream put on disk.
func (w *FileWriter) Written() []string { return w.written }

// ReadJSON loads one of the agent's own notes. A missing file is not an error.
func ReadJSON(path string, into any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	if len(data) == 0 {
		return nil
	}
	return json.Unmarshal(data, into)
}

// WriteJSON saves one of the agent's own notes, creating .agentforge/ as needed.
func WriteJSON(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}
