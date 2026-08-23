package deploy

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// agentOn builds the whole deployment agent over a temporary store, which is
// what the server holds and what the Studio talks to.
func agentOn(t *testing.T) *Agent {
	t.Helper()
	agent, err := NewAgent(t.TempDir(), nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	return agent
}

// ask makes one request of the agent's own handler.
func ask(t *testing.T, agent *Agent, method, path string, body any) (int, map[string]any) {
	t.Helper()
	var payload *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		payload = bytes.NewReader(raw)
	} else {
		payload = bytes.NewReader(nil)
	}
	request := httptest.NewRequest(method, path, payload)
	recorder := httptest.NewRecorder()
	agent.Handler().ServeHTTP(recorder, request)

	value := map[string]any{}
	if strings.HasPrefix(recorder.Header().Get("Content-Type"), "application/json") {
		_ = json.Unmarshal(recorder.Body.Bytes(), &value)
	}
	return recorder.Code, value
}

func TestTheStudioCanReachEverythingItCalls(t *testing.T) {
	agent := agentOn(t)

	if code, body := ask(t, agent, http.MethodGet, "/api/health", nil); code != 200 ||
		body["status"] != "ok" {
		t.Errorf("health = %d %v", code, body)
	}
	if code, body := ask(t, agent, http.MethodGet, "/api/runs", nil); code != 200 ||
		body["runs"] == nil {
		t.Errorf("runs = %d %v", code, body)
	}
	if code, body := ask(t, agent, http.MethodGet, "/api/onboarding/status", nil); code != 200 ||
		body["tools"] == nil || body["cloud_notice"] == nil {
		t.Errorf("onboarding = %d %v", code, sortedKeys(body))
	}
	if code, body := ask(t, agent, http.MethodPost, "/api/mongodb/check",
		map[string]any{"uri": "nope"}); code != 200 || body["code"] != MongoMalformed {
		t.Errorf("mongodb = %d %v", code, body)
	}
	if code, body := ask(t, agent, http.MethodPost, "/api/aws/bootstrap-role-template",
		map[string]any{"principal_arn": ""}); code != 200 || body["template"] == nil {
		t.Errorf("role template = %d %v", code, sortedKeys(body))
	}
	if code, _ := ask(t, agent, http.MethodPost, "/api/aws/vercel/status",
		map[string]any{"token": ""}); code != 200 {
		t.Errorf("vercel status = %d", code)
	}

	// The routes that need a run all agree about a run that is not there.
	for _, path := range []string{
		"/api/runs/run_nothere", "/api/runs/run_nothere/events",
		"/api/runs/run_nothere/artifacts", "/api/runs/run_nothere/monitor",
	} {
		if code, body := ask(t, agent, http.MethodGet, path, nil); code != 404 {
			t.Errorf("%s = %d %v", path, code, body)
		}
	}
	if code, _ := ask(t, agent, http.MethodGet, "/api/runs/run_nothere/nonsense", nil); code != 404 {
		t.Errorf("an unknown action is a 404, not a crash: %d", code)
	}
}

func TestAnalyzeNeedsConsentAndAProject(t *testing.T) {
	agent := agentOn(t)

	code, body := ask(t, agent, http.MethodPost, "/api/runs/analyze",
		map[string]any{"path": project(t), "target": TargetEC2})
	if code != 400 || !strings.Contains(text(body["error"]), "consent") {
		t.Errorf("analyze without consent = %d %v", code, body)
	}

	code, body = ask(t, agent, http.MethodPost, "/api/runs/analyze",
		map[string]any{"path": filepath.Join(t.TempDir(), "nowhere"),
			"target": TargetEC2, "cloud_consent": true})
	if code != 400 {
		t.Errorf("analyze of nothing = %d %v", code, body)
	}

	code, body = ask(t, agent, http.MethodPost, "/api/runs/analyze",
		map[string]any{"path": project(t), "target": TargetEC2,
			"cloud_consent": true, "validate_container": false})
	if code != 202 || text(body["run_id"]) == "" || body["state"] != string(StateAnalyzing) {
		t.Fatalf("analyze = %d %v", code, body)
	}
	waitFor(t, agent, text(body["run_id"]))
}

// waitFor waits for an analysis to reach whatever it reaches.
func waitFor(t *testing.T, agent *Agent, runID string) *Run {
	t.Helper()
	for i := 0; i < 300; i++ {
		run, err := agent.Store.GetRun(runID)
		if err == nil && run != nil && run.State != StateAnalyzing && run.State != StateDraft {
			return run
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("the analysis never finished")
	return nil
}

func TestTheDeployButtonRunsTheWholeFlow(t *testing.T) {
	agent := agentOn(t)
	source := project(t)

	runID, err := agent.Deploy(context.Background(), StudioRequest{
		Path: source, Target: TargetEC2, AWSProfile: "deployment-agent",
		Region: DefaultRegion, MongoURI: "mongodb+srv://user:pass@cluster/shop",
	})
	if err != nil {
		t.Fatal(err)
	}
	run := waitFor(t, agent, runID)

	// With no model there is no validated plan, so it stops at the review
	// gate — and the reason is on the run where the panel shows it.
	if run.State != StateFailed || !strings.Contains(run.Error, "Ollama") {
		t.Fatalf("state = %s, error = %q", run.State, run.Error)
	}
	records, err := agent.Store.Artifacts(runID)
	if err != nil || len(records) == 0 {
		t.Errorf("the review was not written down: %v", err)
	}

	// And the panel finds it.
	view := agent.ForProject(source)
	if view.Live != nil {
		t.Errorf("a failed run is not live: %+v", view.Live)
	}
	if !view.HaveLast || view.Last["run_id"] != runID || view.Last["state"] != string(StateFailed) {
		t.Errorf("last = %+v", view.Last)
	}
	if view.Last["target"] != TargetEC2 {
		t.Errorf("target = %v", view.Last["target"])
	}
}

func TestForProjectSeparatesProjects(t *testing.T) {
	agent := agentOn(t)
	mine, theirs := project(t), project(t)

	run, err := agent.Store.CreateRun(NewRunID(), "mine", mine, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := agent.Store.Transition(run.ID, StateAnalyzing, nil); err != nil {
		t.Fatal(err)
	}

	view := agent.ForProject(mine)
	if view.Live == nil || !view.HaveLast || view.Live["run_id"] != run.ID {
		t.Errorf("live = %+v last = %+v", view.Live, view.Last)
	}
	if other := agent.ForProject(theirs); other.Live != nil || other.HaveLast {
		t.Errorf("another project's runs leaked: %+v", other)
	}
}

func TestBodiesAreBounded(t *testing.T) {
	agent := agentOn(t)
	huge := strings.Repeat("a", bodyLimit+1)
	request := httptest.NewRequest(http.MethodPost, "/api/mongodb/check",
		strings.NewReader(`{"uri":"`+huge+`"}`))
	recorder := httptest.NewRecorder()
	agent.Handler().ServeHTTP(recorder, request)
	if recorder.Code != 400 {
		t.Errorf("code = %d", recorder.Code)
	}
}

func TestTheButtonWaitsForASlowAnalysis(t *testing.T) {
	agent := agentOn(t)
	run, err := agent.Store.CreateRun(NewRunID(), "shop", t.TempDir(), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := agent.Store.Transition(run.ID, StateAnalyzing, nil); err != nil {
		t.Fatal(err)
	}

	// An analysis that is still going is waited for, however long it takes.
	ctx, cancel := context.WithCancel(context.Background())
	settled := make(chan bool, 1)
	go func() {
		_, ok := agent.awaitReview(ctx, run.ID)
		settled <- ok
	}()

	select {
	case <-settled:
		t.Fatal("it gave up on a run that is still analysing")
	case <-time.After(2500 * time.Millisecond):
	}

	// And it notices the moment the review is ready.
	if _, err := agent.Store.Transition(run.ID, StateReviewReady, nil); err != nil {
		t.Fatal(err)
	}
	select {
	case ok := <-settled:
		if !ok {
			t.Error("a reviewable run was not recognised")
		}
	case <-time.After(5 * time.Second):
		t.Error("it did not notice the review")
	}
	cancel()
}

func TestTheButtonStopsWhenTheRunDoes(t *testing.T) {
	agent := agentOn(t)
	run, err := agent.Store.CreateRun(NewRunID(), "shop", t.TempDir(), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, step := range []State{StateAnalyzing, StateFailed} {
		if _, err := agent.Store.Transition(run.ID, step, nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, ok := agent.awaitReview(context.Background(), run.ID); ok {
		t.Error("a failed analysis is not deployable")
	}

	// A cancelled context stops the wait rather than spinning.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, ok := agent.awaitReview(ctx, run.ID); ok {
		t.Error("a cancelled wait does not deploy anything")
	}
}

func TestTheButtonRefusesWhatSettingsCanFix(t *testing.T) {
	agent := agentOn(t)
	source := project(t)
	complete := StudioRequest{
		Path: source, Target: TargetEC2, AWSProfile: "deployment-agent",
		Region: DefaultRegion, MongoURI: "mongodb+srv://user:pass@cluster/shop",
	}

	cases := map[string]struct {
		change func(StudioRequest) StudioRequest
		want   string
	}{
		"no database": {
			func(r StudioRequest) StudioRequest { r.MongoURI = ""; return r },
			"no production MongoDB URI",
		},
		"a database that is not one": {
			func(r StudioRequest) StudioRequest { r.MongoURI = "postgres://host/db"; return r },
			"MONGODB_URI must contain",
		},
		"no AWS account": {
			func(r StudioRequest) StudioRequest { r.AWSProfile = ""; return r },
			"no AWS account connected",
		},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := agent.Deploy(context.Background(), c.change(complete)); err == nil ||
				!strings.Contains(err.Error(), c.want) {
				t.Fatalf("err = %v, want something about %q", err, c.want)
			}
			// Nothing was started, so nothing has to be cleaned up.
			if view := agent.ForProject(source); view.Live != nil || view.HaveLast {
				t.Errorf("a refused deployment left a run behind: %+v", view)
			}
		})
	}

	// Vercel needs no AWS profile.
	vercel := complete
	vercel.Target, vercel.AWSProfile = TargetVercel, ""
	runID, err := agent.Deploy(context.Background(), vercel)
	if err != nil {
		t.Fatalf("vercel = %v", err)
	}

	// And a second deployment of the same project waits for the first.
	if _, err := agent.Deploy(context.Background(), vercel); err == nil ||
		!strings.Contains(err.Error(), "already running") {
		t.Errorf("err = %v", err)
	}
	waitFor(t, agent, runID)
}

func TestTheProjectViewCarriesWhatThePanelDraws(t *testing.T) {
	agent := agentOn(t)
	source := project(t)

	runID, err := agent.Deploy(context.Background(), StudioRequest{
		Path: source, Target: TargetEC2, AWSProfile: "deployment-agent",
		Region: DefaultRegion, MongoURI: "mongodb+srv://user:pass@cluster/shop",
	})
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, agent, runID)

	view := agent.ForProject(source)
	if !view.HaveLast {
		t.Fatal("the finished run is not there")
	}
	// The panel reads the provider block under this name, not "repo".
	if _, wrong := view.Last["repo"]; wrong {
		t.Error("the provider block is under the old key")
	}
	for _, key := range []string{"run_id", "state", "target", "readiness", "repo_state",
		"error", "monitor", "events_count", "link", "artifacts_current"} {
		if _, present := view.Last[key]; !present {
			t.Errorf("last has no %q", key)
		}
	}
	if number(view.Last["events_count"]) == 0 {
		t.Error("the finished run counted no events")
	}
	if object(view.Last["link"])["adopted_at"] == "" {
		t.Error("the record has no time on it")
	}
}

func TestALiveRunReportsItsProgress(t *testing.T) {
	agent := agentOn(t)
	source := project(t)
	run, err := agent.Store.CreateRun(NewRunID(), "shop", source, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := agent.Store.Transition(run.ID, StateAnalyzing, nil); err != nil {
		t.Fatal(err)
	}
	for _, event := range []Event{
		{Type: EventStep, Stage: "intake", Status: StatusComplete, Percent: 14,
			Message: "Detected 1 Next.js service(s)"},
		{Type: EventLog, Stage: "planner", Status: StatusRunning, Percent: 0,
			Message: "a log line reports no progress"},
		{Type: EventStep, Stage: "planner", Status: StatusRunning, Percent: 18,
			Message: "Planning with a model"},
	} {
		if err := agent.Store.AddEvent(run.ID, event); err != nil {
			t.Fatal(err)
		}
	}

	live := agent.ForProject(source).Live
	if live == nil {
		t.Fatal("a run in flight is not shown as live")
	}
	if live["phase"] != "planner" || number(live["percent"]) != 18 {
		t.Errorf("progress = %v %v", live["phase"], live["percent"])
	}
	if live["message"] != "Planning with a model" {
		t.Errorf("message = %v", live["message"])
	}
	events, _ := live["events"].([]Event)
	if len(events) != 3 {
		t.Fatalf("the panel draws its pipeline from these: %v", live["events"])
	}
	if events[0].Stage != "intake" || events[0].Status != StatusComplete {
		t.Errorf("events = %+v", events)
	}
}

func TestAFinishedDeploymentIsWrittenIntoItsProject(t *testing.T) {
	agent := agentOn(t)
	source := project(t)
	run, err := agent.Store.CreateRun(NewRunID(), "corner-shop", source, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := agent.Store.AddEvent(run.ID, Event{Type: EventStep, Stage: "deploy",
		Status: StatusComplete, Message: "GitHub Actions completed"}); err != nil {
		t.Fatal(err)
	}
	for _, step := range []State{StateAnalyzing, StateReviewReady, StateBootstrapping,
		StateCIRunning, StateDeploying, StateValidating, StateLive} {
		if _, err := agent.Store.Transition(run.ID, step, nil); err != nil {
			t.Fatalf("%s: %v", step, err)
		}
	}

	// Nothing has been deployed from this project yet, as far as it knows.
	if HasRecord(source) {
		t.Fatal("a project with no deployment claims one")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go agent.settle(ctx, run.ID)

	deadline := time.Now().Add(20 * time.Second)
	for !HasRecord(source) && time.Now().Before(deadline) {
		time.Sleep(100 * time.Millisecond)
	}
	if !HasRecord(source) {
		t.Fatal("the deployment left no record in the project")
	}

	for _, name := range []string{"run.json", "events.json", "monitor.json", "link.json"} {
		if _, err := os.Stat(filepath.Join(source, ".agentforge", "deploy", name)); err != nil {
			t.Errorf("%s is missing: %v", name, err)
		}
	}
	link, err := os.ReadFile(filepath.Join(source, ".agentforge", "deploy", "link.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(link), run.ID) || !strings.Contains(string(link), "LIVE") {
		t.Errorf("link.json = %s", link)
	}

	// Tearing it down files the record away rather than deleting it.
	if err := Retire(source, run.ID); err != nil {
		t.Fatal(err)
	}
	if HasRecord(source) {
		t.Error("the record is still live after a teardown")
	}
	if _, err := os.Stat(filepath.Join(source, ".agentforge", "deploy-archive", run.ID, "run.json")); err != nil {
		t.Errorf("the record was not archived: %v", err)
	}
	if note := Deleted(source); note == nil || note["run_id"] != run.ID {
		t.Errorf("deleted = %v", note)
	}
}

func TestAStrandedRunDoesNotBlockTheButtonForGood(t *testing.T) {
	agent := agentOn(t)
	source := project(t)

	// The app closed between the analysis finishing and the deployment
	// starting. Nothing sweeps this state up, so counting it as in flight
	// would refuse every later deployment of this project.
	run, err := agent.Store.CreateRun(NewRunID(), "corner-shop", source, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, step := range []State{StateAnalyzing, StateReviewReady} {
		if _, err := agent.Store.Transition(run.ID, step, nil); err != nil {
			t.Fatal(err)
		}
	}

	request := StudioRequest{Path: source, Target: TargetVercel,
		MongoURI: "mongodb+srv://user:pw@cluster.mongodb.net/shop"}
	if err := agent.canDeploy(request, TargetVercel); err != nil {
		t.Fatalf("a stranded run refused a new deployment: %v", err)
	}

	// A run that really is deploying still does.
	if _, err := agent.Store.Transition(run.ID, StateBootstrapping, nil); err != nil {
		t.Fatal(err)
	}
	if err := agent.canDeploy(request, TargetVercel); err == nil ||
		!strings.Contains(err.Error(), "already running") {
		t.Errorf("err = %v", err)
	}
}

func TestTheRecordLeftInTheProjectCarriesNoAccountNumber(t *testing.T) {
	agent := agentOn(t)
	source := project(t)
	run, err := agent.Store.CreateRun(NewRunID(), "corner-shop", source, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := agent.Store.Update(run.ID, map[string]any{"repo": map[string]any{
		"account_id":     "123456789012",
		"ecr_repository": "123456789012.dkr.ecr.ap-south-1.amazonaws.com/shop",
	}}); err != nil {
		t.Fatal(err)
	}
	current, err := agent.Store.GetRun(run.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := Adopt(agent.Store, current); err != nil {
		t.Fatal(err)
	}

	// This folder is the folder that gets committed and pushed.
	body, err := os.ReadFile(filepath.Join(source, ".agentforge", "deploy", "run.json"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body), "123456789012") {
		t.Errorf("the AWS account number was written into the project:\n%s", body)
	}
	if !strings.Contains(string(body), "***ACCOUNT***") {
		t.Errorf("run.json = %s", body)
	}
}

func TestATornDownProjectStopsSayingItHasADeployment(t *testing.T) {
	agent := agentOn(t)
	source := project(t)
	run, err := agent.Store.CreateRun(NewRunID(), "corner-shop", source, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, step := range []State{StateAnalyzing, StateReviewReady, StateBootstrapping,
		StateCIRunning, StateDeploying, StateValidating, StateLive} {
		if _, err := agent.Store.Transition(run.ID, step, nil); err != nil {
			t.Fatal(err)
		}
	}
	live, err := agent.Store.GetRun(run.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := Adopt(agent.Store, live); err != nil {
		t.Fatal(err)
	}

	// Teardown files the record away — the one path that used to leave the
	// project reporting a deployment that no longer exists.
	if err := agent.Deployer.destroyed(live); err != nil {
		t.Fatal(err)
	}
	if HasRecord(source) {
		t.Error("the project still claims a deployment it no longer has")
	}
	view := agent.ForProject(source)
	if view.Deleted == nil {
		t.Errorf("the panel has nothing to say about the deleted deployment: %+v", view)
	}
}

func TestARunKnowsItsTargetBeforeItIsPlanned(t *testing.T) {
	agent := agentOn(t)
	ctx, cancel := context.WithCancel(context.Background())
	runID, err := agent.Analyzer.Start(ctx, project(t), TargetVercel, false)
	cancel()
	if err != nil {
		t.Fatal(err)
	}
	run, err := agent.Store.GetRun(runID)
	if err != nil {
		t.Fatal(err)
	}
	// The panel draws its pipeline and its heading from the target, for the
	// whole of an intake that happens long before there is a plan to read it
	// out of. Without this a Vercel deployment says it is going to AWS.
	if TargetOf(run) != TargetVercel {
		t.Errorf("target = %q, want %q", TargetOf(run), TargetVercel)
	}
	// Let the cancelled analysis finish putting itself away before the
	// temporary directory under it goes.
	waitFor(t, agent, runID)
}
