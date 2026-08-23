package deploy

import (
	"context"
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
	runID, err := s.Analyzer.Start(ctx, request.Path, target, request.ValidateBuild)
	if err != nil {
		return "", err
	}

	go s.deployWhenReviewed(ctx, runID, request)
	return runID, nil
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

// ForProject is what the Studio's deploy panel shows for one project: the run
// happening now, if any, and the last one that finished.
func (s *Agent) ForProject(path string) (live, last map[string]any) {
	wanted, err := filepath.Abs(path)
	if err != nil {
		return nil, nil
	}
	wanted = strings.ToLower(wanted)

	runs, err := s.Store.ListRuns(250)
	if err != nil {
		return nil, nil
	}
	for i := range runs {
		run := &runs[i]
		mine, err := filepath.Abs(run.ProjectPath)
		if err != nil || strings.ToLower(mine) != wanted {
			continue
		}
		// ListRuns is newest first, so the first match of each kind is the
		// one the panel wants.
		record := s.public(run)
		record["run_id"] = run.ID
		record["state"] = string(run.State)
		record["target"] = TargetOf(run)
		if last == nil {
			last = record
		}
		if live == nil && !Terminal[run.State] {
			live = record
		}
		if live != nil && last != nil {
			break
		}
	}
	return live, last
}
