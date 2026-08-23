package deploy

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// analyzed runs the whole analysis graph over the fixture, with the local
// build turned off — there is no network here to install from, and what the
// graph does around the build is what these tests are about.
func analyzed(t *testing.T, target string) (*Analyzer, *Run, []Event) {
	t.Helper()
	seen := []Event{}
	analyzer := &Analyzer{
		Store: testStore(t),
		Emit:  func(_ string, event Event) { seen = append(seen, event) },
	}
	run, err := analyzer.Store.CreateRun(NewRunID(), "corner-shop", project(t),
		filepath.Join(t.TempDir(), "staged"))
	if err != nil {
		t.Fatal(err)
	}
	if err := analyzer.Analyze(context.Background(), run, target, false); err != nil {
		t.Fatal(err)
	}
	after, err := analyzer.Store.GetRun(run.ID)
	if err != nil {
		t.Fatal(err)
	}
	return analyzer, after, seen
}

func TestAnalysisEndsOnTheReviewScreen(t *testing.T) {
	analyzer, run, seen := analyzed(t, TargetEC2)

	// Validation requires a model-backed plan, and there is no model here, so
	// the run stops at FAILED with the reason on it — which is the behaviour
	// a machine with no Ollama has always had.
	if run.State != StateFailed {
		t.Fatalf("state = %s", run.State)
	}
	if !strings.Contains(run.Error, "validated Ollama plan is required") {
		t.Errorf("error = %q", run.Error)
	}

	records, err := analyzer.Store.Artifacts(run.ID)
	if err != nil || len(records) == 0 {
		t.Fatalf("the artifacts were not recorded: %v", err)
	}
	// Everything it generated is still there to look at, whatever the gate said.
	for _, record := range records {
		if _, err := os.Stat(filepath.Join(run.StagedPath, filepath.FromSlash(record.Path))); err != nil {
			t.Errorf("%s was recorded but not written", record.Path)
		}
	}

	gates := object(object(run.Readiness)["gates"])
	if gates["model_used"] != false || gates["artifacts_valid"] != false {
		t.Errorf("gates = %+v", gates)
	}
	if last := seen[len(seen)-1]; last.Stage != "review" || last.Status != StatusFailed {
		t.Errorf("last event = %+v", last)
	}
}

func TestAnalysisWalksEveryStage(t *testing.T) {
	_, _, seen := analyzed(t, TargetEC2)
	stages := map[string]bool{}
	for _, event := range seen {
		stages[event.Stage] = true
	}
	for _, stage := range []string{"analysis", "intake", "planner", "runtime", "cicd",
		"aws", "generator", "security", "review"} {
		if !stages[stage] {
			t.Errorf("nothing was reported for %q (saw %v)", stage, sortedKeys(stages))
		}
	}
}

func TestAnalysisRecordsTheSpecAndPlan(t *testing.T) {
	_, run, _ := analyzed(t, TargetVercel)
	if run.ProjectName != "corner-shop" {
		t.Errorf("project = %q", run.ProjectName)
	}
	if text(run.Plan["project_slug"]) != "corner-shop" || run.Plan["target"] != TargetVercel {
		t.Errorf("plan = %+v", run.Plan)
	}
	services, _ := run.Spec["services"].([]any)
	if len(services) != 1 {
		t.Errorf("spec = %+v", run.Spec)
	}
	// The environment contract is on the plan, so the deploy screen can ask
	// for exactly the values the app needs.
	entries := object(run.Plan["environment"])["entries"]
	if list, _ := entries.([]any); len(list) != 4 {
		t.Errorf("contract = %v", entries)
	}
}

func TestScoreBuild(t *testing.T) {
	base := Readiness{Categories: Categories{CICD: 20, Provider: 25, Security: 20}}

	plan := &Plan{}
	if never := scoreBuild(base, buildResult{}, plan); never.Categories.Build != 0 || len(plan.Risks) != 0 {
		t.Errorf("a build that never ran says nothing: %+v", never)
	}

	plan = &Plan{}
	failed := scoreBuild(base, buildResult{Attempted: true}, plan)
	if failed.Categories.Build != 8 || !warned(plan.Risks, "Local build validation failed") {
		t.Errorf("failed = %+v %v", failed, plan.Risks)
	}

	plan = &Plan{}
	passed := scoreBuild(base, buildResult{Attempted: true, Passed: true}, plan)
	if passed.Categories.Build != 15 || passed.Score != 80 {
		t.Errorf("passed = %+v", passed)
	}

	plan = &Plan{}
	vulnerable := scoreBuild(base, buildResult{Attempted: true, Passed: true,
		Findings: map[string]int{"total": 4, "critical": 1}}, plan)
	if vulnerable.Categories.Security != 8 {
		t.Errorf("a critical finding costs the security score: %+v", vulnerable)
	}
	if !warned(plan.Risks, "vulnerability finding(s)") {
		t.Errorf("risks = %v", plan.Risks)
	}

	plan = &Plan{}
	moderate := scoreBuild(base, buildResult{Attempted: true, Passed: true,
		Findings: map[string]int{"total": 2}}, plan)
	if moderate.Categories.Security != 16 {
		t.Errorf("moderate = %+v", moderate)
	}
}

func TestDependencyFindings(t *testing.T) {
	output := `added 402 packages, and audited 403 packages in 12s

7 vulnerabilities (3 moderate, 3 high, 1 critical)`
	got := dependencyFindings(output)
	if got["total"] != 7 || got["moderate"] != 3 || got["high"] != 3 || got["critical"] != 1 {
		t.Errorf("findings = %v", got)
	}
	if clean := dependencyFindings("found 0 vulnerabilities"); clean["total"] != 0 {
		t.Errorf("clean = %v", clean)
	}
}

func TestRouteAfterBuild(t *testing.T) {
	cases := []struct {
		result buildResult
		model  bool
		want   string
	}{
		{buildResult{Attempted: true, Passed: false}, true, "repair"},
		{buildResult{Attempted: true, Passed: false}, false, "review"},
		{buildResult{Attempted: true, Passed: true}, true, "review"},
		{buildResult{}, true, "review"},
	}
	for _, c := range cases {
		got := routeAfterBuild(context.Background(),
			&state{Result: c.result, Plan: &Plan{ModelUsed: c.model}})
		if got != c.want {
			t.Errorf("routeAfterBuild(%+v, model=%v) = %q", c.result, c.model, got)
		}
	}
}

func TestStartRefusesAProjectItCannotRead(t *testing.T) {
	analyzer := &Analyzer{Store: testStore(t)}
	if _, err := analyzer.Start(context.Background(), "", TargetEC2, false); err == nil {
		t.Error("a path is required")
	}
	if _, err := analyzer.Start(context.Background(),
		filepath.Join(t.TempDir(), "nowhere"), TargetEC2, false); err == nil {
		t.Error("a folder that does not exist is an error")
	}
	if _, err := analyzer.Start(context.Background(), t.TempDir(), "aws_lambda", false); err == nil {
		t.Error("an unsupported target is an error")
	}
}

func TestTheFinalScoreSaysWhatWasProved(t *testing.T) {
	analyzer, run, _ := analyzed(t, TargetEC2)
	body, err := os.ReadFile(filepath.Join(run.StagedPath, "readiness-score.json"))
	if err != nil {
		t.Fatal(err)
	}
	var readiness map[string]any
	if err := json.Unmarshal(body, &readiness); err != nil {
		t.Fatal(err)
	}
	gates, _ := readiness["gates"].(map[string]any)
	if gates == nil {
		t.Fatalf("the file a reviewer reads does not say what passed: %s", body)
	}
	for _, gate := range []string{"model_used", "artifacts_valid", "build_validation",
		"security_validation"} {
		if _, present := gates[gate]; !present {
			t.Errorf("%s is missing from %v", gate, sortedKeys(gates))
		}
	}
	// And it is the same score the run record carries.
	stored := object(run.Readiness)
	if number(stored["score"]) != number(readiness["score"]) {
		t.Errorf("the file and the record disagree: %v vs %v", readiness["score"], stored["score"])
	}
	records, _ := analyzer.Store.Artifacts(run.ID)
	for _, record := range records {
		if record.Path == "readiness-score.json" && record.SHA256 != SHA256(body) {
			t.Error("the recorded hash is of an older version of the file")
		}
	}
}

func TestARepairedBuildIsNotStillReportedAsFailed(t *testing.T) {
	base := Readiness{Categories: Categories{CICD: 20, Provider: 25, Security: 20}}
	plan := &Plan{}

	// The first attempt fails and says so.
	failed := scoreBuild(base, buildResult{Attempted: true}, plan)
	if failed.Categories.Build != 8 || !warned(plan.Risks, "Local build validation failed") {
		t.Fatalf("first attempt = %+v %v", failed, plan.Risks)
	}

	// The bounded repair fixes it and the second attempt passes: the review
	// screen must not still say the build failed.
	passed := scoreBuild(base, buildResult{Attempted: true, Passed: true}, plan)
	if passed.Categories.Build != 15 {
		t.Errorf("second attempt = %+v", passed)
	}
	if warned(plan.Risks, "Local build validation failed") {
		t.Errorf("the withdrawn risk is still on the plan: %v", plan.Risks)
	}

	// Anything else the run recorded stays.
	plan = &Plan{Risks: []string{"Ollama planning was unavailable; deterministic safe defaults were used."}}
	_ = scoreBuild(base, buildResult{Attempted: true, Passed: true}, plan)
	if len(plan.Risks) != 1 {
		t.Errorf("an unrelated risk was dropped: %v", plan.Risks)
	}
}
