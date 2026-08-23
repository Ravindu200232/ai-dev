package deploy

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func abandoned(t *testing.T, state State, age time.Duration, repo map[string]any) (*Supervisor, *Run) {
	t.Helper()
	store := testStore(t)
	deployer := NewDeployer(store, nil)
	supervisor := &Supervisor{Store: store, Deployer: deployer,
		Monitor: &Monitor{Store: store}}

	run, err := store.CreateRun(NewRunID(), "shop", t.TempDir(), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	fields := map[string]any{}
	if repo != nil {
		fields["repo"] = repo
	}
	if len(fields) > 0 {
		if _, err := store.Update(run.ID, fields); err != nil {
			t.Fatal(err)
		}
	}
	// Walk the run into the state it was abandoned in.
	for _, step := range path(state) {
		if _, err := store.Transition(run.ID, step, nil); err != nil {
			t.Fatal(err)
		}
	}
	backdate(t, store, run.ID, age)
	after, _ := store.GetRun(run.ID)
	return supervisor, after
}

// backdate makes a run look like it was last touched a while ago, which is
// what the application closing mid-deployment leaves behind. Every write
// stamps the time, so the record has to be edited where it lies.
func backdate(t *testing.T, store *Store, id string, age time.Duration) {
	t.Helper()
	path := filepath.Join(store.runDir(id), "run.json")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var record map[string]any
	if err := json.Unmarshal(body, &record); err != nil {
		t.Fatal(err)
	}
	record["updated_at"] = time.Now().UTC().Add(-age).Format(time.RFC3339Nano)
	updated, err := json.Marshal(record)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, updated, 0o644); err != nil {
		t.Fatal(err)
	}
}

// path is the sequence of transitions that reaches a state from DRAFT.
func path(state State) []State {
	switch state {
	case StateAnalyzing:
		return []State{StateAnalyzing}
	case StateBootstrapping:
		return []State{StateAnalyzing, StateReviewReady, StateBootstrapping}
	case StateDeploying:
		return []State{StateAnalyzing, StateReviewReady, StateBootstrapping,
			StateCIRunning, StateDeploying}
	}
	return nil
}

func TestAnAbandonedAnalysisIsGivenLongEnough(t *testing.T) {
	supervisor, run := abandoned(t, StateAnalyzing, 10*time.Minute, nil)
	moved, err := supervisor.Once(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 0 {
		t.Fatalf("an analysis ten minutes in is still working: %+v", moved)
	}

	supervisor, run = abandoned(t, StateAnalyzing, 2*time.Hour, nil)
	moved, err = supervisor.Once(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].To != string(StateFailed) {
		t.Fatalf("moved = %+v", moved)
	}
	after, _ := supervisor.Store.GetRun(run.ID)
	if after.State != StateFailed || after.Error == "" {
		t.Errorf("run = %+v", after)
	}
}

func TestAnAbandonedDeploymentThatNeverReachedGitHubFails(t *testing.T) {
	supervisor, run := abandoned(t, StateBootstrapping, 30*time.Minute, nil)
	moved, _ := supervisor.Once(context.Background())
	if len(moved) != 1 || moved[0].From != string(StateBootstrapping) {
		t.Fatalf("moved = %+v", moved)
	}
	after, _ := supervisor.Store.GetRun(run.ID)
	if after.State != StateFailed {
		t.Errorf("state = %s", after.State)
	}
}

func TestARunAWorkerStillOwnsIsLeftAlone(t *testing.T) {
	supervisor, run := abandoned(t, StateBootstrapping, 30*time.Minute, nil)
	key, err := filepath.Abs(run.ProjectPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := supervisor.Deployer.reserve(run.ID, strings.ToLower(key)); err != nil {
		t.Fatal(err)
	}
	moved, _ := supervisor.Once(context.Background())
	if len(moved) != 0 {
		t.Fatalf("a run with a live worker is not abandoned: %+v", moved)
	}
}

func TestAFreshDeploymentIsNotReconciled(t *testing.T) {
	supervisor, _ := abandoned(t, StateDeploying, time.Minute, nil)
	moved, _ := supervisor.Once(context.Background())
	if len(moved) != 0 {
		t.Fatalf("a deployment a minute old is still going: %+v", moved)
	}
}

func TestCheckMongoReadsTheURIBeforeConnecting(t *testing.T) {
	cases := []struct {
		uri  string
		code string
	}{
		{"", MongoMalformed},
		{"postgres://host/db", MongoMalformed},
		{"mongodb+srv://user:pass@cluster/db?appname=x&frobnicate=1", MongoMalformed},
		{"mongodb+srv://user:pass@ cluster/db", MongoMalformed},
	}
	for _, c := range cases {
		got := CheckMongo(context.Background(), c.uri)
		if got.OK || got.Code != c.code {
			t.Errorf("CheckMongo(%q) = %+v", c.uri, got)
		}
	}

	// An option the driver knows is not a reason to refuse.
	unsupported := CheckMongo(context.Background(),
		"mongodb+srv://user:pass@cluster/db?frobnicate=1")
	if len(unsupported.UnknownOptions) != 1 || unsupported.UnknownOptions[0] != "frobnicate" {
		t.Errorf("unknown = %+v", unsupported)
	}
	if !warned([]string{unsupported.Message}, "Copy the connection string from Atlas") {
		t.Errorf("message = %q", unsupported.Message)
	}
}

func TestMongoFailureExplainsItself(t *testing.T) {
	cases := map[string]string{
		"bad auth : Authentication failed":                  MongoAuth,
		"server selection error: context deadline exceeded": MongoUnreachable,
		"lookup cluster.mongodb.net: no such host":          MongoDNS,
		"something nobody has seen before":                  MongoFailed,
	}
	for message, code := range cases {
		got := mongoFailure(errorString(message), "shop")
		if got.Code != code {
			t.Errorf("mongoFailure(%q) = %q, want %q", message, got.Code, code)
		}
		if got.OK || got.Detail == "" {
			t.Errorf("failure = %+v", got)
		}
	}

	// The detail is where a connection string would leak.
	leaky := mongoFailure(errorString("cannot reach mongodb+srv://user:hunter2@cluster/db"), "")
	if warned([]string{leaky.Detail}, "hunter2") {
		t.Errorf("detail = %q", leaky.Detail)
	}
}

type errorString string

func (e errorString) Error() string { return string(e) }
