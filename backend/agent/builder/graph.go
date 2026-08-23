package builder

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/paulnegz/langgraphgo/graph"

	"agentforge/agent/app"
	"agentforge/agent/core"
	"agentforge/agent/qa"
	"agentforge/agent/server"
)

// The build is a graph, not a script: the model's plan decides how many times
// the build node runs, and the coverage check decides whether the plan needs
// more tasks before the runtime stages start.
//
//	intake → plan → build ⟲ → coverage ⟲ plan
//	                            ↓
//	  dev → unit → api → e2e_plan → e2e → perf → security → summary → preview
//
// Every node re-surveys the project with ls before it decides anything, which
// is why nothing here depends on an earlier node's memory of the tree.

const maxCoverageRounds = 3

// Agent is what the server dispatches to.
type Agent struct{}

func NewAgent() *Agent { return &Agent{} }

// Handle runs one instruction from the Studio.
func (a *Agent) Handle(run *core.Run, msg server.Message) (string, error) {
	switch msg.Type {
	case "agent_build", "agent_resume":
		return NewPipeline(msg).Run(run)

	case "agent_update", "feature", "element_edit", "pencil_edit":
		return app.Edit(run.Context(), run, requestFor(msg))

	case "image_edit", "image_swap":
		return "", errors.New("picture editing is not part of this build")
	}
	return "", fmt.Errorf("unknown instruction %q", msg.Type)
}

// requestFor translates a Studio message into an edit request.
func requestFor(msg server.Message) app.Request {
	kind := map[string]string{
		"agent_update": app.KindRepair,
		"feature":      app.KindFeature,
		"element_edit": app.KindSelect,
		"pencil_edit":  app.KindPencil,
	}[msg.Type]

	return app.Request{
		Kind:    kind,
		Prompt:  msg.Prompt,
		Route:   firstNonEmpty(msg.Route, msg.Str("route")),
		Element: msg.Map("element"),
		Strokes: msg.Raw["strokes"],
		Console: msg.Str("console"),
	}
}

// Pipeline holds the state one build needs that is not part of the run itself.
type Pipeline struct {
	msg   server.Message
	suite *qa.Suite

	gaps           []string
	coverageRounds int
	scaffolded     bool
}

func NewPipeline(msg server.Message) *Pipeline {
	return &Pipeline{msg: msg, suite: qa.NewSuite()}
}

// Run compiles the graph and drives it to the end.
func (p *Pipeline) Run(run *core.Run) (string, error) {
	defer p.suite.Close()

	compiled, err := p.compile()
	if err != nil {
		return "", err
	}
	if _, err := compiled.Invoke(run.Context(), run); err != nil {
		if run.Cancelled() {
			return "", core.ErrCancelled
		}
		return "", unwrapNode(err)
	}
	return p.preview(run), nil
}

func (p *Pipeline) compile() (*graph.StateRunnable, error) {
	g := graph.NewStateGraph()

	g.AddNode("intake", p.intake)
	g.AddNode("plan", p.plan)
	g.AddNode("build", p.build)
	g.AddNode("coverage", p.coverage)
	g.AddNode("dev", p.dev)
	g.AddNode("unit", p.unit)
	g.AddNode("api", p.api)
	g.AddNode("e2e_plan", p.e2ePlan)
	g.AddNode("e2e", p.e2e)
	g.AddNode("perf", p.perf)
	g.AddNode("security", p.security)
	g.AddNode("summary", p.summary)
	g.AddNode("preview", p.previewNode)

	g.SetEntryPoint("intake")
	g.AddEdge("intake", "plan")
	g.AddEdge("plan", "build")
	g.AddConditionalEdge("build", p.afterBuild)       // more tasks, or coverage
	g.AddConditionalEdge("coverage", p.afterCoverage) // gaps go back to plan
	g.AddConditionalEdge("dev", p.stopOrGo("unit"))
	g.AddConditionalEdge("unit", p.stopOrGo("api"))
	g.AddConditionalEdge("api", p.stopOrGo("e2e_plan"))
	g.AddConditionalEdge("e2e_plan", p.stopOrGo("e2e"))
	g.AddConditionalEdge("e2e", p.stopOrGo("perf"))
	g.AddConditionalEdge("perf", p.stopOrGo("security"))
	g.AddConditionalEdge("security", p.stopOrGo("summary"))
	g.AddEdge("summary", "preview")
	g.AddEdge("preview", graph.END)

	return g.Compile()
}

// stopOrGo skips the rest of the pipeline once the run has been cancelled.
// The graph runner does not check the context between nodes, so this does.
func (p *Pipeline) stopOrGo(next string) func(context.Context, any) string {
	return func(_ context.Context, state any) string {
		if state.(*core.Run).Cancelled() {
			return "preview"
		}
		return next
	}
}

// --- QA stages ---------------------------------------------------------------
//
// Each one refreshes the survey first, then hands off to the qa package. A
// stage that fails does not abort the build: the report records it and the
// pipeline carries on, because a failing security scan should not cost the user
// the application that was just built.

func (p *Pipeline) dev(ctx context.Context, state any) (any, error) {
	run := state.(*core.Run)
	if err := run.Check(); err != nil {
		return run, err
	}
	p.refresh(run)
	run.Step("build", "done")
	run.Step("test", "active")
	run.Progress("dev", 50)
	if err := p.suite.Dev(ctx, run); err != nil {
		return run, err // without a running app nothing after this can run
	}
	return run, nil
}

func (p *Pipeline) unit(ctx context.Context, state any) (any, error) {
	return p.stage(ctx, state, "unit", 58, p.suite.Unit)
}

func (p *Pipeline) api(ctx context.Context, state any) (any, error) {
	return p.stage(ctx, state, "api", 74, p.suite.API)
}

func (p *Pipeline) e2ePlan(ctx context.Context, state any) (any, error) {
	return p.stage(ctx, state, "e2e-plan", 78, p.suite.E2EPlan)
}

func (p *Pipeline) e2e(ctx context.Context, state any) (any, error) {
	return p.stage(ctx, state, "e2e", 82, p.suite.E2E)
}

func (p *Pipeline) perf(ctx context.Context, state any) (any, error) {
	return p.stage(ctx, state, "performance", 92, p.suite.Performance)
}

func (p *Pipeline) security(ctx context.Context, state any) (any, error) {
	return p.stage(ctx, state, "security", 94, p.suite.Security)
}

// stage runs one QA step and keeps going if it fails.
func (p *Pipeline) stage(ctx context.Context, state any, label string, pct float64,
	fn func(context.Context, *core.Run) error) (any, error) {

	run := state.(*core.Run)
	if err := run.Check(); err != nil {
		return run, err
	}
	p.refresh(run)
	run.Progress(label, pct)
	if err := fn(ctx, run); err != nil {
		if run.Cancelled() {
			return run, nil
		}
		run.Warn(label + " did not finish: " + err.Error())
		run.Errors = append(run.Errors, label+": "+err.Error())
	}
	return run, nil
}

// summary reads the finished application and writes the note every later edit
// starts from.
func (p *Pipeline) summary(ctx context.Context, state any) (any, error) {
	run := state.(*core.Run)
	p.refresh(run)
	run.Progress("summary", 97)
	if err := app.Summarize(ctx, run); err != nil {
		run.Warn("the app summary could not be written: " + err.Error())
	}
	p.suite.Save(run)
	return run, nil
}

// previewNode closes the run out. It is also where a cancelled run lands, so
// the dev server is always stopped and the test rail always closes.
func (p *Pipeline) previewNode(_ context.Context, state any) (any, error) {
	run := state.(*core.Run)
	run.Step("test", "done")
	if run.Cancelled() {
		return run, nil
	}
	run.Step("preview", "done")
	run.Progress("preview", 100)

	route := p.preview(run)
	run.Info("🌐 preview ready at " + core.DevURL() + route)
	if len(run.Errors) > 0 {
		run.Warn(fmt.Sprintf("%d stage(s) reported problems — see the Testing tab", len(run.Errors)))
	}
	return run, nil
}

// preview picks the route to open: the app's own home, or the first page it has.
func (p *Pipeline) preview(run *core.Run) string {
	if run.Structure == nil {
		return "/"
	}
	for _, route := range run.Structure.Routes {
		if route == "/" {
			return "/"
		}
	}
	for _, route := range run.Structure.Routes {
		if !strings.Contains(route, "[") {
			return route
		}
	}
	return "/"
}

// unwrapNode strips the graph runner's wrapper so the Studio sees the real
// message rather than "error in node build: …".
func unwrapNode(err error) error {
	msg := err.Error()
	if i := strings.Index(msg, ": "); strings.HasPrefix(msg, "error in node ") && i > 0 {
		return errors.New(msg[i+2:])
	}
	return err
}
