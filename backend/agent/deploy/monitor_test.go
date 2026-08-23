package deploy

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestScoreIsEvidence(t *testing.T) {
	run := &Run{
		Plan: map[string]any{"target": TargetEC2},
		Repo: map[string]any{"push": map[string]any{"head_sha": "abc123"}},
		// 15 for the build and 20 for security are what the review phase awards.
		Readiness: map[string]any{"categories": map[string]any{"build": 15, "security": 20}},
	}
	snapshot := map[string]any{
		"workflow": map[string]any{"conclusion": "success"},
		"aws": map[string]any{
			"instances": []map[string]any{{"state": "running"}},
			"releases":  []map[string]any{{"status": "Success"}},
		},
		"logs": []map[string]any{{"message": "listening on 4010"}},
		"api": []Probe{
			{Name: "Homepage", Passed: true},
			{Name: "Health API", Passed: true},
		},
	}

	got := Score(run, snapshot)
	if got.Score != 100 {
		t.Errorf("score = %d (%+v)", got.Score, got.Categories)
	}
	if got.Phase != "live" {
		t.Errorf("phase = %q", got.Phase)
	}

	// One failing probe is the difference between deployed and working.
	snapshot["api"] = []Probe{{Name: "Homepage", Passed: true}, {Name: "Health API", Passed: false}}
	if failing := Score(run, snapshot); failing.Categories.API != 0 || failing.Phase != "deploying" {
		t.Errorf("a failing probe = %+v", failing)
	}
}

func TestProviderHealthNeedsTheCommitThatWasPushed(t *testing.T) {
	run := &Run{
		Plan: map[string]any{"target": TargetECS},
		Repo: map[string]any{"push": map[string]any{"head_sha": "abc123"}},
	}
	running := map[string]any{"aws": map[string]any{"services": []map[string]any{{
		"desired_count": 1, "running_count": 1, "rollout_state": "COMPLETED",
		"image": "1234.dkr.ecr.eu-west-1.amazonaws.com/shop:abc123",
	}}}}
	if !providerHealthy(run, running) {
		t.Error("a service running the pushed commit is healthy")
	}

	stale := map[string]any{"aws": map[string]any{"services": []map[string]any{{
		"desired_count": 1, "running_count": 1, "rollout_state": "COMPLETED",
		"image": "1234.dkr.ecr.eu-west-1.amazonaws.com/shop:older",
	}}}}
	if providerHealthy(run, stale) {
		t.Error("a service still running the previous image is not this deployment")
	}

	starting := map[string]any{"aws": map[string]any{"services": []map[string]any{{
		"desired_count": 2, "running_count": 1, "rollout_state": "IN_PROGRESS",
		"image": "1234.dkr.ecr.eu-west-1.amazonaws.com/shop:abc123",
	}}}}
	if providerHealthy(run, starting) {
		t.Error("half the tasks running is not running")
	}
}

func TestVercelHealthNeedsAReadyDeployment(t *testing.T) {
	run := &Run{
		Plan: map[string]any{"target": TargetVercel},
		Repo: map[string]any{"push": map[string]any{"head_sha": "abc123"}},
	}
	ready := map[string]any{"vercel": map[string]any{"deployments": []map[string]any{
		{"ready_state": "READY", "commit_sha": "abc123"},
	}}}
	if !providerHealthy(run, ready) {
		t.Error("a READY deployment of the pushed commit is healthy")
	}
	building := map[string]any{"vercel": map[string]any{"deployments": []map[string]any{
		{"ready_state": "BUILDING", "commit_sha": "abc123"},
	}}}
	if providerHealthy(run, building) {
		t.Error("a build in progress is not a deployment")
	}
}

func TestProbeApplication(t *testing.T) {
	site := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == DefaultHealthPath {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer site.Close()

	probes := probeApplication(context.Background(), site.URL)
	if len(probes) != 2 {
		t.Fatalf("probes = %+v", probes)
	}
	if !probes[0].Passed || probes[0].Status != 200 {
		t.Errorf("homepage = %+v", probes[0])
	}
	if probes[1].Passed || probes[1].Status != 503 {
		t.Errorf("health = %+v", probes[1])
	}
	if allProbesPassed(probes) {
		t.Error("one failing probe fails the set")
	}
	if len(probeApplication(context.Background(), "")) != 0 {
		t.Error("nothing deployed, nothing to ask")
	}
}

func TestMaskHidesIdentifiers(t *testing.T) {
	masked := mask(map[string]any{
		"arn":   "arn:aws:iam::123456789012:role/deploy",
		"image": "repo@sha256:1c9f6a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f",
		"port":  4010,
		"list":  []any{"account 210987654321 again"},
	})
	got := object(masked)
	if got["arn"] != "arn:aws:iam::***ACCOUNT***:role/deploy" {
		t.Errorf("arn = %v", got["arn"])
	}
	if got["image"] != "repo@sha256:***masked***" {
		t.Errorf("image = %v", got["image"])
	}
	if got["port"] != 4010 {
		t.Errorf("a number is not masked: %v", got["port"])
	}
	list, _ := got["list"].([]any)
	if len(list) != 1 || list[0] != "account ***ACCOUNT*** again" {
		t.Errorf("list = %v", list)
	}
}

func TestSnapshotOfARunThatHasDeployedNothing(t *testing.T) {
	store := testStore(t)
	monitor := &Monitor{Store: store}
	run, err := store.CreateRun(NewRunID(), "shop", t.TempDir(), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}

	snapshot, err := monitor.Snapshot(context.Background(), run.ID)
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"captured_at", "github", "aws", "vercel", "logs", "api",
		"errors", "workflow", "readiness"} {
		if _, present := snapshot[key]; !present {
			t.Errorf("the snapshot has no %q", key)
		}
	}
	if number(object(snapshot["readiness"])["score"]) != 20 {
		t.Errorf("a run with nothing deployed scores only its review: %v", snapshot["readiness"])
	}

	// It is stored, so the Studio can show the last snapshot when the
	// provider cannot be reached.
	stored, err := store.GetRun(run.ID)
	if err != nil || len(stored.Monitor) == 0 {
		t.Fatalf("the snapshot was not kept: %v", err)
	}
	if _, err := monitor.Snapshot(context.Background(), NewRunID()); err == nil {
		t.Error("a run that does not exist has no snapshot")
	}
}

func TestASnapshotKeepsWhatTheReviewProved(t *testing.T) {
	deployer, run := deployable(t)
	monitor := &Monitor{Store: deployer.Store}

	if _, err := monitor.Snapshot(context.Background(), run.ID); err != nil {
		t.Fatal(err)
	}
	after, err := deployer.Store.GetRun(run.ID)
	if err != nil {
		t.Fatal(err)
	}
	gates := object(object(after.Readiness)["gates"])
	if gates["build_validation"] != true || gates["security_validation"] != true {
		t.Fatalf("the review's evidence was thrown away: %+v", after.Readiness)
	}
	// So the run can still be deployed after a snapshot.
	if _, _, err := deployer.check(request(after)); err != nil {
		t.Errorf("a snapshot made the run undeployable: %v", err)
	}
}

func TestEverySnapshotProblemSurvivesAndIsMasked(t *testing.T) {
	snapshot := map[string]any{"errors": []string{}}
	noteProblem(snapshot, errorString("logs filter-log-events: AccessDenied for account 123456789012"))
	noteProblem(snapshot, errorString("ec2 describe-instances: InvalidInstanceID.NotFound"))

	problems, _ := snapshot["errors"].([]string)
	if len(problems) != 2 {
		t.Fatalf("problems = %v", problems)
	}
	// Masking has to reach inside the list, which is the one place a raw
	// provider error is kept verbatim.
	masked, _ := mask(problems).([]string)
	if len(masked) != 2 {
		t.Fatalf("masked = %v", masked)
	}
	if strings.Contains(masked[0], "123456789012") {
		t.Errorf("an account number reached the snapshot: %q", masked[0])
	}
	if !strings.Contains(masked[0], "***ACCOUNT***") || !strings.Contains(masked[1], "NotFound") {
		t.Errorf("masked = %v", masked)
	}
}
