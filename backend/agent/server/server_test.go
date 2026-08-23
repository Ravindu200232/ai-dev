package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"agentforge/agent/core"
)

// recorder stands in for a connected Studio socket.
type recorder struct {
	mu     sync.Mutex
	events []map[string]any
}

func (r *recorder) Send(data []byte) {
	var event map[string]any
	if err := json.Unmarshal(data, &event); err != nil {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events = append(r.events, event)
}

func (r *recorder) typed(name string) []map[string]any {
	r.mu.Lock()
	defer r.mu.Unlock()
	var out []map[string]any
	for _, e := range r.events {
		if e["type"] == name {
			out = append(out, e)
		}
	}
	return out
}

// Every event studio/lib/ws.js switches on must serialise with the keys that
// file reads. Renaming one here silently breaks the UI, so pin them all.
func TestEventContract(t *testing.T) {
	rec := &recorder{}
	hub := core.NewHub()
	hub.Add(rec)
	run := core.NewRun(context.Background(), hub, core.Paths{Projects: t.TempDir()},
		core.NewLLM(), "demo", "build")

	run.Log("INFO", "hello")
	run.ProjectExists("demo")
	run.Step("build", "active")
	run.Progress("build", 42)
	run.PhaseUpsert("t1", "Create the orders page", "active")
	run.File("app/page.jsx", 12, "body")
	run.StreamStart("app/page.jsx")
	run.StreamToken("const")
	run.StreamEnd("app/page.jsx", "const x = 1")
	run.TestStart()
	run.TestRun(2)
	run.TestResult("pass", "orders render", "3 cases")
	run.TestFixing(2, []string{"boom"})
	run.E2E(map[string]any{"state": "journey_start", "title": "Place an order", "index": 0, "total": 4})
	run.Detected("next", "app-router")
	run.ChatIntent("edit", "rename the button")
	run.AgentMsg("thinking")
	run.Command("npm run dev")
	run.ElementPicked("app/page.jsx", 12)
	run.UndoPoint("u1", []string{"app/page.jsx"})
	run.FeaturePlan(map[string]any{"steps": 1})
	run.DemoAccounts([]any{})
	run.Ask("scope", "app/page.jsx", "/orders", nil, []string{"a", "b"})
	run.Failed("it broke")
	run.Cancelled_(nil)
	run.Done("/orders")

	// key -> the fields studio/lib/ws.js reads off that event
	want := map[string][]string{
		"log":            {"level", "text"},
		"project":        {"project"},
		"step":           {"step", "status"},
		"progress":       {"step", "pct"},
		"phase":          {"phase", "title", "status"},
		"file":           {"name", "content"},
		"stream_start":   {"file"},
		"stream":         {"token"},
		"stream_end":     {"file", "content"},
		"test_start":     {},
		"test_run":       {"attempt"},
		"test_result":    {"status", "msg", "detail"},
		"test_fixing":    {"attempt", "errors"},
		"e2e_event":      {"state"},
		"detected":       {"site_type", "strategy"},
		"chat_intent":    {"intent", "summary"},
		"agent_msg":      {"text"},
		"command":        {},
		"element_picked": {"file", "line"},
		"undo_point":     {"id", "files"},
		"feature_plan":   {},
		"demo_accounts":  {},
		"ask":            {"kind", "file", "route", "routes", "options"},
		"error":          {"text"},
		"cancelled":      {"project"},
		"done":           {"url", "project", "preview"},
	}
	for kind, keys := range want {
		got := rec.typed(kind)
		if len(got) == 0 {
			t.Errorf("no %q event was emitted", kind)
			continue
		}
		for _, key := range keys {
			if _, ok := got[0][key]; !ok {
				t.Errorf("%q event is missing %q: %v", kind, key, got[0])
			}
		}
	}
}

// A run that emits `ask` with no routes must still send an array, because the
// Studio does `m.routes || []` but then maps over it.
func TestAskSendsArrays(t *testing.T) {
	rec := &recorder{}
	hub := core.NewHub()
	hub.Add(rec)
	run := core.NewRun(context.Background(), hub, core.Paths{Projects: t.TempDir()}, core.NewLLM(), "d", "build")
	run.Ask("scope", "", "", nil, nil)

	event := rec.typed("ask")[0]
	if _, ok := event["routes"].([]any); !ok {
		t.Errorf("routes should be an array, got %T", event["routes"])
	}
	if _, ok := event["options"].([]any); !ok {
		t.Errorf("options should be an array, got %T", event["options"])
	}
}

func TestParseMessageKeepsRaw(t *testing.T) {
	msg, err := ParseMessage([]byte(`{"type":"element_edit","project":"shop","prompt":"bigger",
		"element":{"file":"app/page.jsx","line":9},"think":true}`))
	if err != nil {
		t.Fatal(err)
	}
	if msg.Type != "element_edit" || msg.Project != "shop" || msg.Prompt != "bigger" {
		t.Fatalf("typed fields wrong: %+v", msg)
	}
	if !msg.Think {
		t.Error("think was dropped")
	}
	el := msg.Map("element")
	if el == nil || el["file"] != "app/page.jsx" {
		t.Errorf("the untyped payload was lost: %v", msg.Raw)
	}
}

func TestParseMessageRejectsTypeless(t *testing.T) {
	if _, err := ParseMessage([]byte(`{"project":"shop"}`)); err == nil {
		t.Error("a message with no type should be refused")
	}
	if _, err := ParseMessage([]byte(`not json`)); err == nil {
		t.Error("unparseable input should be refused")
	}
}

// fakeAgent lets a dispatch test run without a model.
type fakeAgent struct {
	started chan Message
	block   chan struct{}
	err     error
}

func (f *fakeAgent) Handle(_ *core.Run, msg Message) (string, error) {
	f.started <- msg
	if f.block != nil {
		<-f.block
	}
	return "/done", f.err
}

func TestDispatchRefusesSecondRun(t *testing.T) {
	agent := &fakeAgent{started: make(chan Message, 2), block: make(chan struct{})}
	s := newTestServer(t, agent)

	if err := s.Dispatch(Message{Type: "agent_build", Project: "a"}); err != nil {
		t.Fatalf("first dispatch: %v", err)
	}
	<-agent.started // it is now in flight

	if err := s.Dispatch(Message{Type: "agent_build", Project: "b"}); err == nil {
		t.Error("a second run should be refused while one is in progress")
	}
	close(agent.block)
}

func TestDispatchRejectsUnknownType(t *testing.T) {
	s := newTestServer(t, &fakeAgent{started: make(chan Message, 1)})
	if err := s.Dispatch(Message{Type: "nonsense"}); err == nil {
		t.Error("an unknown instruction should be refused")
	}
}

// A handler that fails must still close the run, or the Studio stays busy.
func TestFailedRunStillEmitsTerminalEvent(t *testing.T) {
	rec := &recorder{}
	agent := &fakeAgent{started: make(chan Message, 1), err: errors.New("nope")}
	s := newTestServer(t, agent)
	s.Hub.Add(rec)

	if err := s.Dispatch(Message{Type: "agent_build", Project: "a"}); err != nil {
		t.Fatal(err)
	}
	<-agent.started
	waitFor(t, func() bool { return len(rec.typed("error")) > 0 })

	if got := rec.typed("error")[0]["text"]; got != "nope" {
		t.Errorf("error text = %v", got)
	}
}

func TestCancelActive(t *testing.T) {
	agent := &fakeAgent{started: make(chan Message, 1), block: make(chan struct{})}
	s := newTestServer(t, agent)
	rec := &recorder{}
	s.Hub.Add(rec)

	if s.CancelActive() {
		t.Error("there is nothing to cancel yet")
	}
	_ = s.Dispatch(Message{Type: "agent_build", Project: "a"})
	<-agent.started
	if !s.CancelActive() {
		t.Fatal("the in-flight run should have been cancelled")
	}
	close(agent.block)
	waitFor(t, func() bool { return len(rec.typed("cancelled")) > 0 })
}

func TestJobStoreLifecycle(t *testing.T) {
	store := newJobStore()
	release := make(chan struct{})
	started := store.Start("/tune", func() (int, any, error) {
		<-release
		return 200, map[string]any{"prompt": "ok"}, nil
	})
	if started.Status != "running" {
		t.Fatalf("a new job should be running, got %q", started.Status)
	}
	if got := store.Poll(started.ID)["status"]; got != "running" {
		t.Errorf("poll = %v, want running", got)
	}
	close(release)
	waitFor(t, func() bool { return store.Poll(started.ID)["status"] == "done" })

	if got := store.Poll("job_missing")["status"]; got != "unknown" {
		t.Errorf("an expired job should poll as unknown, got %v", got)
	}
}

func TestAPIRouting(t *testing.T) {
	s := newTestServer(t, &fakeAgent{started: make(chan Message, 1)})
	srv := httptest.NewServer(s.router())
	defer srv.Close()

	// Ours, and answered as JSON.
	resp, err := http.Get(srv.URL + "/__agentforge/api/projects")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("GET /projects = %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Errorf("Content-Type = %q", ct)
	}

	// Ours, but not a route we serve.
	resp2, err := http.Get(srv.URL + "/__agentforge/api/nope")
	if err != nil {
		t.Fatal(err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != 404 {
		t.Errorf("an unknown endpoint should 404, got %d", resp2.StatusCode)
	}
}

func TestCancelEndpoint(t *testing.T) {
	s := newTestServer(t, &fakeAgent{started: make(chan Message, 1)})
	srv := httptest.NewServer(s.router())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/__agentforge/api/build/cancel", "application/json", strings.NewReader("{}"))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body["stopped"] != false {
		t.Errorf("nothing was running, so stopped should be false: %v", body)
	}
}

func TestRedactURI(t *testing.T) {
	cases := map[string]string{
		"mongodb://user:secret@host:27017/db": "mongodb://•••@host:27017/db",
		"mongodb://127.0.0.1:27017":           "mongodb://127.0.0.1:27017",
		"":                                    "",
	}
	for in, want := range cases {
		if got := redactURI(in); got != want {
			t.Errorf("redactURI(%q) = %q, want %q", in, got, want)
		}
	}
}

func newTestServer(t *testing.T, agent Agent) *Server {
	t.Helper()
	paths := core.Paths{Base: t.TempDir(), Projects: t.TempDir()}
	s := New(core.NewHub(), paths, core.NewLLM(), NewSidecars(paths), NewMongo(paths))
	s.Agent = agent
	return s
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("condition was never met")
}
