package deploy

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// --- the Studio's one button ---------------------------------------------------------------

// StudioRequest is what the Deploy button sends, plus what the Studio has
// already collected in Settings: the account to deploy into, the database the
// app will use, and the token for the one target that needs one.
type StudioRequest struct {
	Path          string
	Target        string
	ValidateBuild bool

	AWSProfile  string
	Region      string
	MongoURI    string
	VercelToken string
}

// Deploy is the whole flow behind one button: read the project, plan it,
// generate and check the files, and — only if all of that passed — deploy it.
//
// The button is the approval. Everything the review would have shown is still
// written down and still has to pass; what the customer does not have to do is
// approve the same deployment twice.
func (s *Agent) Deploy(ctx context.Context, request StudioRequest) (string, error) {
	target := request.Target
	if target == "" {
		target = TargetEC2
	}
	if err := s.canDeploy(request, target); err != nil {
		return "", err
	}
	runID, err := s.Analyzer.Start(ctx, request.Path, target, request.ValidateBuild)
	if err != nil {
		return "", err
	}

	go s.deployWhenReviewed(ctx, runID, request)
	return runID, nil
}

// canDeploy refuses before anything happens, for the reasons a customer can do
// something about. Finding out at the end of a twenty-minute analysis that
// there is no database configured is a worse way to learn it.
func (s *Agent) canDeploy(request StudioRequest, target string) error {
	if strings.TrimSpace(request.MongoURI) == "" {
		return badRequest("no production MongoDB URI — set one in Settings")
	}
	if err := CheckMongoURI(strings.TrimSpace(request.MongoURI)); err != nil {
		return err
	}
	if strings.HasPrefix(target, "aws_") && strings.TrimSpace(request.AWSProfile) == "" {
		return badRequest("no AWS account connected — sign in from Settings")
	}
	for _, run := range s.runsFor(request.Path) {
		if stillWorking(run) {
			return conflict("a deployment for this project is already running")
		}
	}
	return nil
}

// stillWorking is a run that would really be interrupted by starting another one:
// one that is deploying, or an analysis young enough to still be working.
//
// A run stuck before either — the app closed between the analysis finishing
// and the deployment starting — is not going anywhere and nothing sweeps it
// up, so counting it would leave the project's Deploy button refusing for
// good. The supervisor already decides when a run has been abandoned; this
// asks the same question rather than a second one.
func stillWorking(run *Run) bool {
	if Active[run.State] {
		return true
	}
	return (run.State == StateAnalyzing || run.State == StateDraft) &&
		!older(run, time.Now().UTC().Add(-analysisGrace))
}

// Status is what the Studio shows about the agent itself. It is part of this
// process, so it is answering whenever the process is — the Studio's panel
// gates on `listening`, and a service that cannot say so is a tab nobody can
// open.
func (s *Agent) Status() map[string]any {
	return map[string]any{
		"state": "ready", "running": true, "listening": true,
		"in_process": true, "error": "",
	}
}

// deployWhenReviewed waits for the analysis to finish and then deploys what it
// produced. An analysis that failed stops here: the run says why, and the
// console already showed it.
func (s *Agent) deployWhenReviewed(ctx context.Context, runID string, request StudioRequest) {
	run, ok := s.awaitReview(ctx, runID)
	if !ok {
		return
	}
	region := strings.TrimSpace(request.Region)
	if region == "" {
		region = DefaultRegion
	}

	err := s.Deployer.Start(ctx, Request{
		RunID: run.ID, AWSProfile: strings.TrimSpace(request.AWSProfile), Region: region,
		MongoURI: strings.TrimSpace(request.MongoURI), Approved: true,
		VercelToken: strings.TrimSpace(request.VercelToken),
	})
	if err == nil {
		// However it ends, the project keeps the record of it.
		go s.settle(ctx, run.ID)
		return
	}
	_, _ = s.Store.Transition(runID, StateFailed, map[string]any{"error": err.Error()})
	s.Deployer.emit(runID).send(Event{Type: EventError, Stage: "deploy", Status: StatusFailed,
		Percent: 100, Message: err.Error()})
}

// awaitReview follows the run until the analysis has finished, and reports
// whether what it left behind is deployable.
//
// It waits on the state rather than on a clock. An analysis installs the
// project's dependencies and builds it, which on a large project and a slow
// machine is tens of minutes, and a wall-clock limit here would abandon a run
// that was still working — leaving it in REVIEW_READY with nothing watching it
// and no button to press. The supervisor already decides when an analysis is
// lost; this follows that decision instead of making a second one.
func (s *Agent) awaitReview(ctx context.Context, runID string) (*Run, bool) {
	for {
		if !sleep(ctx, time.Second) {
			return nil, false
		}
		run, err := s.Store.GetRun(runID)
		if err != nil || run == nil {
			return nil, false
		}
		switch run.State {
		case StateDraft, StateAnalyzing:
			continue
		case StateReviewReady:
			return run, true
		default:
			// Failed, or cancelled by the customer. Either way the run itself
			// already says why, where the Studio is looking.
			return nil, false
		}
	}
}

// --- one project's deployments -----------------------------------------------------------

// Terminal are the states a run does not come back from. The Studio has the
// same list, and stops polling when it sees one.
var Terminal = set(StateLive, StateFailed, StateRolledBack, StateDestroyed, StateCancelled)

// liveEvents is how much of a run's log the panel needs to draw its pipeline.
// Every stage appears at least once well inside this, and the whole log is a
// request away on the monitor tab.
const liveEvents = 200

// Project is what the Studio's deploy panel shows for one project.
type Project struct {
	Live     map[string]any `json:"live"`
	Last     map[string]any `json:"last"`
	Deleted  map[string]any `json:"deleted,omitempty"`
	HaveLast bool           `json:"-"`
}

// ForProject is the run happening now, if any, and the last one that finished.
//
// The two are shaped differently on purpose: the live one is a progress report,
// so it carries the run's log and where it has got to; the finished one is a
// record, so it carries what was deployed and where it ended up.
func (s *Agent) ForProject(path string) Project {
	out := Project{}
	runs := s.runsFor(path)
	for _, run := range runs {
		// runsFor is newest first, so the first match of each kind is the one
		// the panel wants.
		if !out.HaveLast {
			out.Last, out.HaveLast = s.finished(run), true
		}
		if out.Live == nil && !Terminal[run.State] {
			out.Live = s.progress(run)
		}
		if out.Live != nil && out.HaveLast {
			break
		}
	}
	// The note a teardown left. A project whose newest run was torn down has
	// nothing deployed any more, whatever that run still says it built.
	if len(runs) == 0 || runs[0].State == StateDestroyed {
		out.Deleted = Deleted(path)
	}
	return out
}

// runsFor are one project's runs, newest first. A project reaches here spelled
// several ways — relative, with a different case — so the paths are compared
// absolute and lower-cased.
func (s *Agent) runsFor(path string) []*Run {
	wanted, err := filepath.Abs(path)
	if err != nil {
		return nil
	}
	wanted = strings.ToLower(wanted)

	all, err := s.Store.ListRuns(250)
	if err != nil {
		return nil
	}
	out := []*Run{}
	for i := range all {
		mine, err := filepath.Abs(all[i].ProjectPath)
		if err != nil || strings.ToLower(mine) != wanted {
			continue
		}
		out = append(out, &all[i])
	}
	return out
}

// finished is a run as the "last deployment" panel reads it: what it deployed,
// where it ended up, and how to get back to the evidence.
func (s *Agent) finished(run *Run) map[string]any {
	events, _ := s.Store.Events(run.ID, 0, 0)

	return map[string]any{
		"run_id":    run.ID,
		"state":     string(run.State),
		"target":    TargetOf(run),
		"readiness": object(mask(run.Readiness)),
		// The Studio reads the provider block under this name.
		"repo_state": object(mask(Redact(run.Repo))),
		// Masked like everything else that leaves this process: an IAM refusal
		// is stored with the account number in it.
		"error":        text(mask(run.Error)),
		"monitor":      object(mask(run.Monitor)),
		"events_count": len(events),
		"link": map[string]any{
			"run_id":     run.ID,
			"adopted_at": run.UpdatedAt,
			"state":      string(run.State),
			"target":     TargetOf(run),
		},
		"artifact_schema_version": s.schemaVersion(run),
		"artifacts_current":       s.schemaVersion(run) == ArtifactVersion,
	}
}

// progress is a run in flight: the pipeline the panel draws, how far it has
// got, and the address as soon as there is one.
func (s *Agent) progress(run *Run) map[string]any {
	events, _ := s.Store.Events(run.ID, 0, 0)
	if len(events) > liveEvents {
		events = events[len(events)-liveEvents:]
	}

	phase, message, percent := "", "", 0
	for _, event := range events {
		// A log line reports no progress of its own; a step does.
		if event.Type == EventLog || event.Stage == "" {
			continue
		}
		phase = event.Stage
		if event.Message != "" {
			message = event.Message
		}
		if event.Percent > percent {
			percent = event.Percent
		}
	}
	if message == "" && len(events) > 0 {
		message = events[len(events)-1].Message
	}

	return map[string]any{
		"run_id":  run.ID,
		"project": run.ProjectName,
		"state":   string(run.State),
		"target":  TargetOf(run),
		"phase":   phase,
		"percent": percent,
		"message": text(mask(message)),
		"error":   text(mask(run.Error)),
		"url":     text(mask(object(run.Repo)["application_url"])),
		"events":  maskEvents(events),
	}
}

// maskEvents is a run's log as the panel reads it. The store redacts every
// event on the way in; this takes out what is not a secret but is still nobody
// else's business, the same as every other way out of this process.
func maskEvents(events []Event) []Event {
	for i := range events {
		events[i].Message = text(mask(events[i].Message))
		if len(events[i].Data) > 0 {
			events[i].Data = object(mask(events[i].Data))
		}
	}
	return events
}

// schemaVersion is which renderer produced the artifacts sitting in the run's
// staging copy, so the Studio can say when they are too old to deploy.
func (s *Agent) schemaVersion(run *Run) int {
	body, err := os.ReadFile(filepath.Join(run.StagedPath, "deployment-manifest.json"))
	if err != nil {
		return 0
	}
	var manifest struct {
		Version int `json:"version"`
	}
	if json.Unmarshal(body, &manifest) != nil {
		return 0
	}
	return manifest.Version
}

// --- keeping the record with the project ---------------------------------------------------

// settle waits for a deployment to reach a state it does not come back from,
// and then writes what happened into the project it deployed.
func (s *Agent) settle(ctx context.Context, runID string) {
	for {
		if !sleep(ctx, 5*time.Second) {
			return
		}
		run, err := s.Store.GetRun(runID)
		if err != nil || run == nil {
			return
		}
		if !Terminal[run.State] {
			continue
		}
		if run.State == StateDestroyed {
			// Teardown has already filed the record away. There is nothing
			// left to keep, and writing one back would say the project still
			// has a deployment.
			return
		}
		if err := Adopt(s.Store, run); err != nil {
			s.Deployer.emit(runID).send(Event{Type: EventLog, Stage: "record",
				Status: StatusWarning, Percent: 100,
				Message: "Could not save the deployment record: " + RedactText(err.Error())})
		}
		return
	}
}
