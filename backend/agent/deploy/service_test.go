package deploy

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
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
		Path: source, Target: TargetEC2, MongoURI: "mongodb+srv://user:pass@cluster/shop",
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
	live, last := agent.ForProject(source)
	if live != nil {
		t.Errorf("a failed run is not live: %+v", live)
	}
	if last == nil || last["run_id"] != runID || last["state"] != string(StateFailed) {
		t.Errorf("last = %+v", last)
	}
	if last["target"] != TargetEC2 {
		t.Errorf("target = %v", last["target"])
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

	live, last := agent.ForProject(mine)
	if live == nil || last == nil || live["run_id"] != run.ID {
		t.Errorf("live = %+v last = %+v", live, last)
	}
	if live, last := agent.ForProject(theirs); live != nil || last != nil {
		t.Errorf("another project's runs leaked: %+v %+v", live, last)
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
