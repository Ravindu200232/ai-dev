package deploy

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/paulnegz/langgraphgo/graph"

	"agentforge/agent/core"
)

// Analysis is everything that happens before a customer is asked to approve
// anything: read the project, plan it, generate the files, check them, and
// build it here to prove the deployment would build there.
//
// It is one graph because the steps have to happen in this order and each one
// needs what the last produced — and because a failed local build turns into
// one bounded repair and a second attempt, which is a loop the graph makes
// legible rather than three nested conditionals.

// buildStep is one command the local build validation runs.
type buildStep struct {
	label   string
	command string
	timeout time.Duration
}

// vulnerabilities and severities read npm's own summary line. There is no
// machine-readable output from an install, and running the audit separately
// would install everything twice.
var (
	vulnerabilities = regexp.MustCompile(`(?i)(\d+)\s+vulnerabilit(?:y|ies)\b`)
	severityCount   = map[string]*regexp.Regexp{
		"moderate": regexp.MustCompile(`(?i)(\d+)\s+moderate\b`),
		"high":     regexp.MustCompile(`(?i)(\d+)\s+high\b`),
		"critical": regexp.MustCompile(`(?i)(\d+)\s+critical\b`),
	}
)

// SkipBuild turns the local build validation off. It exists for a machine
// where a real install cannot run, and it costs the run its build gate.
const SkipBuild = "DEPLOYMENT_AGENT_SKIP_BUILD_VALIDATION"

// state is what the analysis graph carries between its nodes.
type state struct {
	Run    *Run
	Target string
	Build  bool // whether to validate the build locally

	Spec       *Spec
	Plan       *Plan
	Records    []Artifact
	Contract   Contract
	Validation Validation
	Readiness  Readiness
	Gates      map[string]any
	Result     buildResult

	staged string
	source string
}

// buildResult is what happened when the project was built here.
type buildResult struct {
	Attempted bool           `json:"attempted"`
	Passed    bool           `json:"passed"`
	Output    string         `json:"output,omitempty"`
	Findings  map[string]int `json:"dependency_findings,omitempty"`
}

// Analyzer runs the analysis for one deployment.
type Analyzer struct {
	Store *Store
	LLM   *core.LLM
	Emit  func(runID string, event Event)

	mu       sync.Mutex
	analysis *graph.StateRunnable
}

func (a *Analyzer) emit(runID string) Emit {
	return func(event Event) {
		if a.Emit != nil {
			a.Emit(runID, event)
		}
	}
}

// Start creates the run and analyses it in the background, which is what lets
// the Studio show a console rather than a spinner.
func (a *Analyzer) Start(ctx context.Context, source, target string, validateBuild bool) (string, error) {
	source = strings.TrimSpace(source)
	if source == "" {
		return "", badRequest("Project path is required")
	}
	source, err := filepath.Abs(source)
	if err != nil {
		return "", badRequest("Project path is required")
	}
	if info, err := os.Stat(source); err != nil || !info.IsDir() {
		return "", badRequest("Project folder does not exist: " + source)
	}
	if _, known := Profiles[target]; !known {
		return "", badRequest("Unsupported deployment target: " + target)
	}

	runID := NewRunID()
	staged := filepath.Join(a.Store.runDir(runID), "worktree")
	run, err := a.Store.CreateRun(runID, filepath.Base(source), source, staged)
	if err != nil {
		return "", err
	}
	// The target is what the panel draws its pipeline from, and the plan that
	// carries it is minutes of analysis away. Write it down now, or a Vercel
	// deployment is labelled an AWS one for its whole first stage.
	if updated, err := a.Store.Update(runID, map[string]any{
		"plan": map[string]any{"target": target}}); err == nil {
		run = updated
	}

	go func() {
		if err := a.Analyze(ctx, run, target, validateBuild); err != nil {
			_, _ = a.Store.Transition(runID, StateFailed, map[string]any{"error": err.Error()})
			a.emit(runID).send(Event{Type: EventError, Stage: "analysis", Status: StatusFailed,
				Percent: 100, Message: err.Error()})
		}
	}()
	return runID, nil
}

// Analyze runs the graph. It is exported so a test can run it to completion
// rather than racing a goroutine.
func (a *Analyzer) Analyze(ctx context.Context, run *Run, target string, validateBuild bool) error {
	a.mu.Lock()
	if a.analysis == nil {
		g := graph.NewStateGraph()
		g.AddNode("intake", a.intakeNode)
		g.AddNode("plan", a.planNode)
		g.AddNode("generate", a.generateNode)
		g.AddNode("validate", a.validateNode)
		g.AddNode("build", a.buildNode)
		g.AddNode("repair", a.repairNode)
		g.AddNode("review", a.reviewNode)
		g.SetEntryPoint("intake")
		g.AddEdge("intake", "plan")
		g.AddEdge("plan", "generate")
		g.AddEdge("generate", "validate")
		g.AddEdge("validate", "build")
		// A build that failed gets one bounded repair and one more attempt;
		// anything else goes straight to the review screen.
		g.AddConditionalEdge("build", routeAfterBuild)
		g.AddEdge("repair", "review")
		g.AddEdge("review", graph.END)
		compiled, err := g.Compile()
		if err != nil {
			a.mu.Unlock()
			return err
		}
		a.analysis = compiled
	}
	runnable := a.analysis
	a.mu.Unlock()

	if _, err := a.Store.Transition(run.ID, StateAnalyzing, nil); err != nil {
		return err
	}
	a.emit(run.ID).send(Event{Type: EventState, Stage: "analysis", Status: StatusRunning,
		Percent: 1, Message: "Deployment analysis started"})

	out, err := runnable.Invoke(ctx, &state{
		Run: run, Target: target, Build: validateBuild,
		staged: run.StagedPath, source: run.ProjectPath,
	})
	if err != nil {
		return unwrapNode(err)
	}
	if _, ok := out.(*state); !ok {
		return fmt.Errorf("the graph returned %T, not a state", out)
	}
	return nil
}

// routeAfterBuild is the only branch in the graph.
func routeAfterBuild(_ context.Context, value any) string {
	s := value.(*state)
	if s.Result.Attempted && !s.Result.Passed && s.Plan.ModelUsed {
		return "repair"
	}
	return "review"
}

func unwrapNode(err error) error {
	message := err.Error()
	if strings.HasPrefix(message, "error in node ") {
		if at := strings.Index(message, ": "); at > 0 {
			return errors.New(message[at+2:])
		}
	}
	return err
}

// --- the nodes ------------------------------------------------------------------------------

func (a *Analyzer) intakeNode(ctx context.Context, value any) (any, error) {
	s := value.(*state)
	spec, err := (&Intake{Emit: a.emit(s.Run.ID)}).Read(ctx, s.source, s.staged)
	if err != nil {
		return s, err
	}
	s.Spec = spec
	_, err = a.Store.Update(s.Run.ID, map[string]any{
		"project_name": spec.Name, "spec": asMap(spec),
	})
	return s, err
}

func (a *Analyzer) planNode(ctx context.Context, value any) (any, error) {
	s := value.(*state)
	s.Plan = (&Planner{LLM: a.LLM, Emit: a.emit(s.Run.ID)}).Plan(ctx, s.Spec, s.Target)
	return s, nil
}

func (a *Analyzer) generateNode(_ context.Context, value any) (any, error) {
	s := value.(*state)
	records, contract, err := (&Generator{Emit: a.emit(s.Run.ID)}).
		Generate(s.Spec, s.Plan, s.staged, s.Target)
	if err != nil {
		return s, err
	}
	s.Records, s.Contract = records, contract
	return s, nil
}

func (a *Analyzer) validateNode(_ context.Context, value any) (any, error) {
	s := value.(*state)
	s.Validation = (&Validator{Emit: a.emit(s.Run.ID)}).Validate(s.staged, s.Records, s.Target)
	s.Plan.Risks = append(s.Plan.Risks, s.Validation.Warnings...)

	// The generator wrote the review score; validation is what earns the
	// security part of it.
	s.Readiness = InitialReadiness(s.Records, ProfileFor(s.Target))
	if s.Validation.Passed {
		s.Readiness.Categories.Security = 20
		s.Readiness.Score = s.Readiness.Categories.Total()
	}
	return s, nil
}

func (a *Analyzer) buildNode(ctx context.Context, value any) (any, error) {
	s := value.(*state)
	if !s.Validation.Passed || !s.Build || os.Getenv(SkipBuild) == "1" {
		return s, nil
	}
	s.Result = a.build(ctx, s)
	s.Readiness = scoreBuild(s.Readiness, s.Result, s.Plan)
	return s, nil
}

// repairNode is the second chance: one bounded repair, and if it changed
// anything, the validation and the build again.
func (a *Analyzer) repairNode(ctx context.Context, value any) (any, error) {
	s := value.(*state)
	emit := a.emit(s.Run.ID)
	planner := &Planner{LLM: a.LLM, Emit: emit}

	actions, err := planner.RepairBuild(ctx, s.Spec, s.Plan, s.Result.Output)
	if err != nil {
		s.Plan.Risks = append(s.Plan.Risks,
			"The bounded Ollama build repair did not produce a valid safe action.")
		emit.send(Event{Type: EventLog, Stage: "repair", Status: StatusFailed, Percent: 93,
			Message: RedactText(err.Error())})
		s.Readiness = scoreBuild(s.Readiness, s.Result, s.Plan)
		return s, nil
	}

	w := writer{source: s.source, staged: s.staged}
	records, repaired := RepairCompatibility(w, s.Spec, s.Plan, s.Records, actions, s.Result.Output)
	s.Records = records
	if repaired {
		s.Validation = (&Validator{Emit: emit}).Validate(s.staged, s.Records, s.Target)
		if s.Validation.Passed {
			s.Result = a.build(ctx, s)
		}
	}
	s.Readiness = scoreBuild(s.Readiness, s.Result, s.Plan)
	return s, nil
}

// reviewNode writes down everything the customer is about to be shown.
func (a *Analyzer) reviewNode(_ context.Context, value any) (any, error) {
	s := value.(*state)
	emit := a.emit(s.Run.ID)

	if s.Result.Passed && len(s.Plan.RepairActions) == 0 {
		emit.step("repair", StatusComplete, 93, "No compatibility repair was required", nil)
	}
	s.Gates = map[string]any{
		"model_used":          s.Plan.ModelUsed,
		"artifacts_valid":     s.Validation.Passed,
		"build_validation":    s.Result.Passed,
		"security_validation": s.Validation.Passed,
	}

	// The report and the score are what the review screen reads, and they
	// were written before the build ran — so they are written again, this
	// time with what the build and the checks proved.
	if records, err := a.finalize(s); err == nil {
		s.Records = records
	}
	if err := a.Store.SetArtifacts(s.Run.ID, s.Records); err != nil {
		return s, err
	}

	readiness := asMap(s.Readiness)
	readiness["gates"] = s.Gates
	fields := map[string]any{"plan": asMap(s.Plan), "readiness": readiness, "error": ""}
	next := StateReviewReady
	if !s.Validation.Passed {
		next = StateFailed
		fields["error"] = strings.Join(s.Validation.Errors, "; ")
	}
	if _, err := a.Store.Transition(s.Run.ID, next, fields); err != nil {
		return s, err
	}

	emit.send(Event{Type: EventState, Stage: "analysis", Status: StatusComplete, Percent: 100,
		Message: "Deployment analysis complete"})

	status, message := StatusComplete,
		"Review is ready. No source or cloud resources have been changed."
	if !s.Validation.Passed {
		status, message = StatusFailed, "Artifact validation failed"
	}
	emit.send(Event{Type: EventState, Stage: "review", Status: status, Percent: 100,
		Message: message,
		Data:    map[string]any{"readiness": readiness, "artifacts": len(s.Records)}})
	return s, nil
}

// finalize rewrites the two files that describe the review, now that the build
// and the repairs have had their say.
func (a *Analyzer) finalize(s *state) ([]Artifact, error) {
	w := writer{source: s.source, staged: s.staged}
	service := planned(s.Spec.Services[0], s.Plan)
	report, err := reportMarkdown(s.Spec, s.Plan, service, s.Readiness, ProfileFor(s.Target))
	if err != nil {
		return s.Records, err
	}

	byPath := map[string]int{}
	for index, record := range s.Records {
		byPath[record.Path] = index
	}
	scored := asMap(s.Readiness)
	if len(s.Gates) > 0 {
		scored["gates"] = s.Gates
	}

	records := append([]Artifact{}, s.Records...)
	for path, body := range map[string]string{
		"deployment-report.md": report,
		"readiness-score.json": indented(scored),
	} {
		record, err := w.write(path, body, "report")
		if err != nil {
			return records, err
		}
		if index, seen := byPath[path]; seen {
			records[index] = record
		} else {
			records = append(records, record)
		}
	}
	return records, nil
}

// scoreBuild turns what the build did into what the review says. A build that
// was never attempted leaves the score alone: it is not evidence either way.
func scoreBuild(readiness Readiness, result buildResult, plan *Plan) Readiness {
	if !result.Attempted {
		return readiness
	}
	if !result.Passed {
		readiness.Categories.Build = 8
		readiness.Score = readiness.Categories.Total()
		if !warnedAbout(plan.Risks, "Local build validation failed") {
			plan.Risks = append(plan.Risks,
				"Local build validation failed; inspect the terminal output before deployment.")
		}
		return readiness
	}

	readiness.Categories.Build = 15
	// The build failed earlier and a bounded repair fixed it: the risk from
	// that first attempt is no longer true, and leaving it on the review
	// screen would contradict the gate right next to it.
	plan.Risks = without(plan.Risks, "Local build validation failed")
	if total := result.Findings["total"]; total > 0 {
		// A vulnerable dependency tree is not a reason to refuse, but it is a
		// reason not to call the deployment secure.
		switch {
		case result.Findings["critical"] > 0:
			readiness.Categories.Security = min(readiness.Categories.Security, 8)
		case result.Findings["high"] > 0:
			readiness.Categories.Security = min(readiness.Categories.Security, 12)
		default:
			readiness.Categories.Security = min(readiness.Categories.Security, 16)
		}
		plan.Risks = append(plan.Risks, "Local dependency installation reported "+
			strconv.Itoa(total)+" vulnerability finding(s), including development dependencies; "+
			"review the CI production dependency audit before deployment.")
	}
	readiness.Score = readiness.Categories.Total()
	return readiness
}

// without drops every risk that mentions something no longer true.
func without(risks []string, needle string) []string {
	out := risks[:0:0]
	for _, risk := range risks {
		if !strings.Contains(risk, needle) {
			out = append(out, risk)
		}
	}
	return out
}

func warnedAbout(risks []string, needle string) bool {
	for _, risk := range risks {
		if strings.Contains(risk, needle) {
			return true
		}
	}
	return false
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// --- building it here first ----------------------------------------------------------------

// build installs and builds the staged copy exactly as CI will. It is the
// difference between a deployment that is going to work and one that looks
// like it will: everything else the agent checks is a file, and a file that
// reads correctly can still fail to compile.
func (a *Analyzer) build(ctx context.Context, s *state) buildResult {
	emit := a.emit(s.Run.ID)
	if !Have("node") {
		emit.send(Event{Type: EventLog, Stage: "build", Status: "skipped", Percent: 86,
			Message: "Node.js not found; build validation skipped"})
		return buildResult{}
	}

	service := planned(s.Spec.Services[0], s.Plan)
	root := s.staged
	if service.Root != "" {
		root = filepath.Join(root, filepath.FromSlash(service.Root))
	}
	build := service.BuildCommand
	if build == "" {
		build = "npm run build"
	}

	transcript := []string{}
	code := 0
	for _, step := range []buildStep{
		{"install", service.InstallCommand, 15 * time.Minute},
		{"build", build, 20 * time.Minute},
	} {
		emit.send(Event{Type: EventTerminal, Stage: "build", Status: StatusRunning, Percent: 87,
			Message: "Running local " + step.label + ": " + step.command})

		parts := strings.Fields(step.command)
		if len(parts) == 0 {
			continue
		}
		out := Exec(ctx, Command{
			Name: parts[0], Args: parts[1:], Dir: root, Timeout: step.timeout,
			// The build gets a database that is not there and placeholder
			// secrets, which is exactly what the CI workflow gives it.
			Env: map[string]string{
				"NODE_ENV":                "",
				"NEXT_TELEMETRY_DISABLED": "1",
				"MONGODB_URI":             BuildMongoURI,
			},
		})
		transcript = append(transcript, "$ "+step.command+"\n"+out.Stdout+"\n"+out.Stderr)
		code = out.Code
		if code != 0 {
			break
		}
	}

	output := RedactText(tail(strings.Join(transcript, "\n"), 12000))
	findings := dependencyFindings(output)

	// Standalone output is what the release actually ships. A build that
	// "passed" without producing it has not proved anything.
	if s.Target == TargetEC2 || s.Target == TargetECS {
		if _, err := os.Stat(filepath.Join(root, ".next", "standalone", "server.js")); err != nil && code == 0 {
			code = 1
			output += "\n[deployment-agent] .next/standalone/server.js was not produced; " +
				"output: 'standalone' is required."
		}
	}

	status, message := StatusComplete, "Local build passed"
	if code != 0 {
		status, message = StatusFailed, "Local build failed"
	}
	emit.send(Event{Type: EventTerminal, Stage: "build", Status: status, Percent: 90,
		Message: message, Data: map[string]any{"output": output, "returncode": code}})

	if findings["total"] > 0 {
		emit.send(Event{Type: EventLog, Stage: "security", Status: StatusWarning, Percent: 92,
			Message: "Dependency installation reported " + strconv.Itoa(findings["total"]) +
				" vulnerability finding(s) (" + strconv.Itoa(findings["high"]) + " high, " +
				strconv.Itoa(findings["critical"]) + " critical)",
			Data: asMap(findings)})
	}
	return buildResult{Attempted: true, Passed: code == 0, Output: output, Findings: findings}
}

// dependencyFindings reads the install's own vulnerability summary. It takes
// the largest number each pattern matched: npm prints a running total and then
// a final one, and the final one is the answer.
func dependencyFindings(output string) map[string]int {
	findings := map[string]int{"total": largest(vulnerabilities, output)}
	for name, pattern := range severityCount {
		findings[name] = largest(pattern, output)
	}
	return findings
}

func largest(pattern *regexp.Regexp, text string) int {
	highest := 0
	for _, match := range pattern.FindAllStringSubmatch(text, -1) {
		if value, err := strconv.Atoi(match[1]); err == nil && value > highest {
			highest = value
		}
	}
	return highest
}
