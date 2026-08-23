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
type Agent struct {
	pictures *app.Pictures
	srs      Specifications
}

func NewAgent(pictures *app.Pictures, specs Specifications) *Agent {
	return &Agent{pictures: pictures, srs: specs}
}

// Handle runs one instruction from the Studio.
func (a *Agent) Handle(run *core.Run, msg server.Message) (string, error) {
	switch msg.Type {
	case "agent_build", "agent_resume":
		return NewPipeline(msg, a.srs).Run(run)

	case "agent_update", "feature", "element_edit", "pencil_edit":
		return app.Edit(run.Context(), run, requestFor(msg))

	case "image_edit", "image_swap":
		return app.Place(run.Context(), run, a.pictures, app.PictureRequest{
			Swap:       msg.Type == "image_swap",
			Prompt:     msg.Prompt,
			Filename:   msg.Str("filename"),
			DataBase64: msg.Str("data_base64"),
			Route:      firstNonEmpty(msg.Route, msg.Str("route")),
			Element:    msg.Map("element"),
		})
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
	srs   Specifications

	gaps           []string
	coverageRounds int
	scaffolded     bool

	// What an earlier attempt at this build already got through. A resume
	// reads these off disk and starts from there instead of writing every
	// file and running every check again.
	resuming  bool
	finished  map[string]bool
	plannedAt string
}

func NewPipeline(msg server.Message, specs Specifications) *Pipeline {
	return &Pipeline{msg: msg, suite: qa.NewSuite(), srs: specs,
		resuming: msg.Type == "agent_resume", finished: map[string]bool{}}
}

// Specifications is where the build contract comes from. It is an interface so
// the builder depends on the one call it makes rather than on the whole SRS
// service — and so a build with no specification needs nothing at all.
type Specifications interface {
	LiveHandoff(ctx context.Context, projectID string) (map[string]any, error)
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
	for _, stage := range qaStages {
		g.AddNode(stage.name, p.stageNode(stage))
	}
	g.AddNode("summary", p.summary)
	g.AddNode("preview", p.previewNode)

	g.SetEntryPoint("intake")
	g.AddEdge("intake", "plan")
	g.AddEdge("plan", "build")
	g.AddConditionalEdge("build", p.afterBuild)       // more tasks, or coverage
	g.AddConditionalEdge("coverage", p.afterCoverage) // gaps go back to plan
	g.AddConditionalEdge("dev", p.stopOrGo(0))
	for i, stage := range qaStages {
		g.AddConditionalEdge(stage.name, p.stopOrGo(i+1))
	}
	g.AddEdge("summary", "preview")
	g.AddEdge("preview", graph.END)

	return g.Compile()
}

// stopOrGo picks the node after this one: the next check that has not already
// passed, or "preview" if the run was cancelled — the graph runner does not
// check the context between nodes, so this does.
func (p *Pipeline) stopOrGo(next int) func(context.Context, any) string {
	return func(_ context.Context, state any) string {
		if state.(*core.Run).Cancelled() {
			return "preview"
		}
		for at := next; at < len(qaStages); at++ {
			if !p.finished[qaStages[at].name] {
				return qaStages[at].name
			}
		}
		// The summary always runs: the application on disk has changed even
		// when none of the checks did.
		return "summary"
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

// qaStages is the rail, in the order it runs. The name is both the graph node
// and the label the Studio shows, so what a resume records as finished is the
// same word everywhere.
type qaStage struct {
	name    string
	percent float64
	run     func(*qa.Suite) func(context.Context, *core.Run) error
}

var qaStages = []qaStage{
	{"unit", 58, func(s *qa.Suite) func(context.Context, *core.Run) error { return s.Unit }},
	{"api", 74, func(s *qa.Suite) func(context.Context, *core.Run) error { return s.API }},
	{"e2e-plan", 78, func(s *qa.Suite) func(context.Context, *core.Run) error { return s.E2EPlan }},
	{"e2e", 82, func(s *qa.Suite) func(context.Context, *core.Run) error { return s.E2E }},
	{"performance", 92, func(s *qa.Suite) func(context.Context, *core.Run) error { return s.Performance }},
	{"security", 94, func(s *qa.Suite) func(context.Context, *core.Run) error { return s.Security }},
}

// stageNode runs one QA step and keeps going if it fails. A step that passed
// is written down, so a resume does not run it a second time.
func (p *Pipeline) stageNode(stage qaStage) func(context.Context, any) (any, error) {
	return func(ctx context.Context, state any) (any, error) {
		run := state.(*core.Run)
		if err := run.Check(); err != nil {
			return run, err
		}
		p.refresh(run)
		run.Progress(stage.name, stage.percent)

		if err := stage.run(p.suite)(ctx, run); err != nil {
			if run.Cancelled() {
				return run, nil
			}
			run.Warn(stage.name + " did not finish: " + err.Error())
			run.Errors = append(run.Errors, stage.name+": "+err.Error())
			return run, nil
		}
		p.finished[stage.name] = true
		p.savePlan(run)
		return run, nil
	}
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
