package deploy

import (
	"context"
	"path/filepath"
	"strings"
	"time"
)

// A run that was mid-flight when the application closed has nobody watching
// it any more. The supervisor is what notices.
//
// It cannot ask a dead worker what happened, so it asks the world instead: for
// a deployment that reached GitHub, a snapshot settles what the workflow did;
// for one that never got that far, enough time passing is the answer. Either
// way the run stops sitting in DEPLOYING for ever, which is the state a
// customer cannot do anything with.

const (
	// SupervisorInterval is how often the sweep runs. It is short because the
	// sweep is cheap for a store with no abandoned runs in it.
	SupervisorInterval = 15 * time.Second

	// orphanGrace is how long a deployment that never reached GitHub is given
	// before it is called lost.
	orphanGrace = 20 * time.Minute

	// analysisGrace is the same for an analysis, which can legitimately take
	// a long time: an install and a production build of a large project.
	analysisGrace = 90 * time.Minute
)

// reconciled are the states a run can be recovered from.
var reconciled = set(StateBootstrapping, StateCIRunning, StateDeploying, StateValidating, StateAnalyzing)

// Recovery is one run the sweep moved.
type Recovery struct {
	RunID string `json:"run_id"`
	From  string `json:"from"`
	To    string `json:"to"`
}

// Supervisor brings abandoned runs back in line with reality.
type Supervisor struct {
	Store    *Store
	Monitor  *Monitor
	Deployer *Deployer
	Emit     func(runID string, event Event)
}

// Watch reconciles on a loop until the context is cancelled. One bad run, or
// one unreachable provider, must not end the loop: the next sweep tries again.
func (s *Supervisor) Watch(ctx context.Context) {
	for {
		if !sleep(ctx, SupervisorInterval) {
			return
		}
		_, _ = s.Once(ctx)
	}
}

// Once is one sweep. It returns what it moved, which is what the Studio's
// recovery notice is built from.
func (s *Supervisor) Once(ctx context.Context) ([]Recovery, error) {
	runs, err := s.Store.ListRuns(250)
	if err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	moved := []Recovery{}

	for i := range runs {
		run := &runs[i]
		if !reconciled[run.State] || s.owned(run) {
			continue
		}
		previous := run.State
		repository := text(object(run.Repo)["repository"])

		switch {
		case previous == StateAnalyzing:
			if !older(run, now.Add(-analysisGrace)) {
				continue
			}
			_, _ = s.Store.Transition(run.ID, StateFailed, map[string]any{
				"error": "Analysis worker did not survive an application restart; re-run analysis.",
			})

		case repository != "":
			// It reached GitHub, so GitHub knows what happened to it.
			if _, err := s.Monitor.Snapshot(ctx, run.ID); err != nil {
				s.emit(run.ID, Event{Type: EventLog, Stage: "supervisor", Status: StatusWarning,
					Message: "Reconciliation deferred: " + RedactText(err.Error())})
				continue
			}

		case older(run, now.Add(-orphanGrace)):
			_, _ = s.Store.Transition(run.ID, StateFailed, map[string]any{
				"error": "Deployment worker did not survive an application restart; re-run analysis.",
			})

		default:
			continue
		}

		current, _ := s.Store.GetRun(run.ID)
		if current == nil || current.State == previous {
			continue
		}
		moved = append(moved, Recovery{RunID: run.ID, From: string(previous), To: string(current.State)})
		s.announce(run.ID, previous, current.State)
	}
	return moved, nil
}

// owned reports whether a live worker in this process is still driving the
// run's project. Those are not abandoned, however long they have been going.
func (s *Supervisor) owned(run *Run) bool {
	if s.Deployer == nil {
		return false
	}
	key, err := filepath.Abs(run.ProjectPath)
	if err != nil {
		// A project path that cannot be resolved cannot be matched against a
		// worker either, so it is left alone rather than declared lost.
		return true
	}
	return s.Deployer.owns(strings.ToLower(key))
}

// older reports whether a run has not been touched since the cutoff.
func older(run *Run, cutoff time.Time) bool {
	updated, err := time.Parse(time.RFC3339Nano, run.UpdatedAt)
	if err != nil {
		return false
	}
	return updated.Before(cutoff)
}

func (s *Supervisor) emit(runID string, event Event) {
	if s.Emit != nil {
		s.Emit(runID, event)
	}
}

func (s *Supervisor) announce(runID string, from, to State) {
	what := "deployment"
	if from == StateAnalyzing {
		what = "analysis"
	}
	status, percent := StatusRunning, 80
	switch to {
	case StateLive:
		status, percent = StatusComplete, 100
	case StateFailed:
		status, percent = StatusFailed, 100
	}
	s.emit(runID, Event{Type: EventState, Stage: "supervisor", Status: status, Percent: percent,
		Message: "Recovered abandoned " + what + ": " + string(from) + " to " + string(to),
		Data:    map[string]any{"previous_state": string(from), "state": string(to)}})
}

// owns reports whether a worker in this process holds the project.
func (d *Deployer) owns(key string) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	_, taken := d.active[key]
	return taken
}
