package srs

import (
	"context"
	"fmt"
	"strings"
	"sync"

	"github.com/paulnegz/langgraphgo/graph"

	"agentforge/agent/core"
)

// Three graphs, matching the Python exactly:
//
//	analysis:      intake → clarify | classify → END
//	generation:    audit → english_plan → generate → render_diagrams → END
//	customization: customize → render_diagrams → END
//
// Every node replaces the whole state, so these are pipelines with one branch
// rather than anything that needs channels or reducers — which is why the
// original only ever imported StateGraph, START and END.

// State is what flows through a graph run.
type State struct {
	ProjectID string
	Project   *Project
	RawIdea   string
	Brief     string
	Language  string

	Classification      map[string]any
	IsNonsense          bool
	NeedsClarification  bool
	ClarificationReason string

	Questions []Question
	Answers   []Answer
	Session   *Session
	Coverage  map[string]any

	Plan         *Plan
	PlanMarkdown string

	Document *Document
	Diagrams []Diagram

	CustomizationPrompt string
	DiffSummary         []string

	Err error
}

// Service owns everything a node needs. It is created once at startup.
type Service struct {
	Repo    *Repo
	LLM     *core.LLM
	Paths   core.Paths
	Storage string // where SRS artifacts are written, per project

	mu       sync.Mutex
	analysis *graph.StateRunnable
}

// NewService wires the SRS service to the store and the model client.
func NewService(repo *Repo, llm *core.LLM, paths core.Paths) *Service {
	return &Service{Repo: repo, LLM: llm, Paths: paths}
}

// The model roles the SRS uses. Writing a specification is planning work, so it
// follows the planner model rather than the coder one.
const (
	roleSRS    = core.RolePlanner
	roleDesign = core.RoleDesign
)

// --- event bus ------------------------------------------------------------------
//
// Events are persisted and read back by the Studio's console. The Python also
// fanned them out over SSE; nothing consumes that, so this only persists.

const (
	channelEvents = "agent_events"
	channelLogs   = "live_logs"
	channelTrace  = "prompt_trace"
	channelErrors = "errors"
)

// emit records one line of what an agent did.
func (s *Service) emit(ctx context.Context, projectID, agent, message string, level string, progress float64, data map[string]any) {
	if level == "" {
		level = "info"
	}
	pct := progress
	_ = s.Repo.AddEvent(ctx, Event{
		ProjectID: projectID, Agent: agent, Channel: channelEvents,
		Level: level, Message: message, Progress: &pct, Data: data,
	})
}

// logf records progress in the live console channel.
func (s *Service) logf(ctx context.Context, projectID, agent string, progress float64, format string, args ...any) {
	pct := progress
	_ = s.Repo.AddEvent(ctx, Event{
		ProjectID: projectID, Agent: agent, Channel: channelLogs,
		Level: "info", Message: fmt.Sprintf(format, args...), Progress: &pct,
	})
}

// warn records something that did not stop the run but is worth seeing.
func (s *Service) warn(ctx context.Context, projectID, agent, message string, progress float64) {
	s.emit(ctx, projectID, agent, message, "warn", progress, nil)
}

// trace records one prompt and its answer, for the trace viewer.
func (s *Service) trace(ctx context.Context, projectID string, payload Doc) {
	_ = s.Repo.AddRecord(ctx, CollTraces, projectID, payload)
}

// recordError keeps a failure where the Studio can show it.
func (s *Service) recordError(ctx context.Context, projectID, agent string, err error) {
	if err == nil {
		return
	}
	_ = s.Repo.AddRecord(ctx, CollErrors, projectID, Doc{
		"agent": agent, "message": err.Error(),
	})
}

// --- graphs ---------------------------------------------------------------------

// RunAnalysis reads the idea and decides whether it can be worked with.
func (s *Service) RunAnalysis(ctx context.Context, state *State) (*State, error) {
	s.mu.Lock()
	if s.analysis == nil {
		g := graph.NewStateGraph()
		g.AddNode("intake", s.intakeNode)
		g.AddNode("classify", s.classifyNode)
		g.AddNode("clarify", s.clarifyNode)
		g.SetEntryPoint("intake")
		g.AddConditionalEdge("intake", routeAfterIntake)
		g.AddEdge("classify", graph.END)
		g.AddEdge("clarify", graph.END)
		compiled, err := g.Compile()
		if err != nil {
			s.mu.Unlock()
			return nil, err
		}
		s.analysis = compiled
	}
	runnable := s.analysis
	s.mu.Unlock()
	return invoke(ctx, runnable, state)
}

// routeAfterIntake is the only branch in any of the three graphs.
func routeAfterIntake(_ context.Context, state any) string {
	if state.(*State).IsNonsense {
		return "clarify"
	}
	return "classify"
}

// RunGeneration and RunCustomization are assembled once their nodes land:
// generation is audit → english_plan → generate → render_diagrams, and
// customization is customize → render_diagrams. auditNode is already written;
// the rest are the composer, the diagram renderer and the editor.

// invoke runs a compiled graph and unwraps the runner's error wrapper so the
// Studio sees the real message.
func invoke(ctx context.Context, runnable *graph.StateRunnable, state *State) (*State, error) {
	out, err := runnable.Invoke(ctx, state)
	if err != nil {
		return state, unwrapNode(err)
	}
	final, ok := out.(*State)
	if !ok {
		return state, fmt.Errorf("the graph returned %T, not a state", out)
	}
	return final, nil
}

func unwrapNode(err error) error {
	msg := err.Error()
	if strings.HasPrefix(msg, "error in node ") {
		if i := strings.Index(msg, ": "); i > 0 {
			return fmt.Errorf("%s", msg[i+2:])
		}
	}
	return err
}

// clarifyNode is where an unreadable idea lands. It asks for the app type
// instead of guessing, which is the only useful thing to do with nonsense.
func (s *Service) clarifyNode(ctx context.Context, state any) (any, error) {
	st := state.(*State)
	s.warn(ctx, st.ProjectID, "InterviewAgent",
		"The idea was hard to read — starting from the app type.", 60)
	st.NeedsClarification = true
	return st, nil
}
