package deploy

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// deployable builds a run that has been through analysis, generation and
// validation, and is sitting on the review screen waiting to be approved.
func deployable(t *testing.T) (*Deployer, *Run) {
	t.Helper()
	store := testStore(t)
	deployer := NewDeployer(store, nil)

	source := project(t)
	staged := filepath.Join(t.TempDir(), "staged")
	spec, err := (&Intake{}).Read(context.Background(), source, staged)
	if err != nil {
		t.Fatal(err)
	}
	plan := (&Planner{}).Plan(context.Background(), spec, TargetEC2)
	plan.ModelUsed = true
	records, _, err := (&Generator{}).Generate(spec, plan, staged, TargetEC2)
	if err != nil {
		t.Fatal(err)
	}
	withModelUsed(t, staged, records)
	// The manifest was rewritten, so its record has to be too.
	for i, record := range records {
		if record.Path == "deployment-manifest.json" {
			body, _ := os.ReadFile(filepath.Join(staged, record.Path))
			records[i].SHA256 = SHA256(body)
			records[i].Size = int64(len(body))
		}
	}

	run, err := store.CreateRun(NewRunID(), spec.Name, source, staged)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SetArtifacts(run.ID, records); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Update(run.ID, map[string]any{
		"spec": asMap(spec),
		"plan": asMap(plan),
		"readiness": map[string]any{
			"score":      70,
			"categories": map[string]any{"build": 20, "cicd": 20, "provider": 25, "security": 20},
			"gates": map[string]any{
				"build_validation": true, "security_validation": true, "artifacts_valid": true,
			},
		},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Transition(run.ID, StateAnalyzing, nil); err != nil {
		t.Fatal(err)
	}
	ready, err := store.Transition(run.ID, StateReviewReady, nil)
	if err != nil {
		t.Fatal(err)
	}
	return deployer, ready
}

func request(run *Run) Request {
	return Request{
		RunID: run.ID, Region: DefaultRegion, Approved: true,
		MongoURI: "mongodb+srv://user:hunter2@cluster.mongodb.net/shop",
	}
}

func TestAReviewedRunPassesEveryGate(t *testing.T) {
	deployer, run := deployable(t)
	if _, _, err := deployer.check(request(run)); err != nil {
		t.Fatalf("a reviewed, validated, approved run must be deployable: %v", err)
	}
}

func TestDeploymentRefusesWhatItShould(t *testing.T) {
	cases := []struct {
		name   string
		break_ func(*Deployer, *Run) Request
		want   string
	}{
		{"unapproved", func(_ *Deployer, run *Run) Request {
			asked := request(run)
			asked.Approved = false
			return asked
		}, "must be explicitly approved"},

		{"unknown run", func(_ *Deployer, run *Run) Request {
			asked := request(run)
			asked.RunID = NewRunID()
			return asked
		}, "Run not found"},

		{"wrong state", func(d *Deployer, run *Run) Request {
			_, _ = d.Store.Transition(run.ID, StateBootstrapping, nil)
			return request(run)
		}, "cannot be deployed from state"},

		{"no model", func(d *Deployer, run *Run) Request {
			plan := object(run.Plan)
			plan["model_used"] = false
			_, _ = d.Store.Update(run.ID, map[string]any{"plan": plan})
			return request(run)
		}, "validated Ollama deployment plan is required"},

		{"build not validated", func(d *Deployer, run *Run) Request {
			_, _ = d.Store.Update(run.ID, map[string]any{"readiness": map[string]any{
				"categories": map[string]any{"build": 0},
				"gates":      map[string]any{"build_validation": false},
			}})
			return request(run)
		}, "Local build validation must pass"},

		{"security not validated", func(d *Deployer, run *Run) Request {
			_, _ = d.Store.Update(run.ID, map[string]any{"readiness": map[string]any{
				"categories": map[string]any{"build": 20},
				"gates":      map[string]any{"build_validation": true, "security_validation": false},
			}})
			return request(run)
		}, "Artifact and security validation must pass"},

		{"artifact changed", func(d *Deployer, run *Run) Request {
			path := filepath.Join(run.StagedPath, ".github", "workflows", "ci.yml")
			body, _ := os.ReadFile(path)
			_ = os.WriteFile(path, append(body, []byte("\n# edited after review\n")...), 0o644)
			return request(run)
		}, "changed after validation"},

		{"artifact removed from the manifest", func(d *Deployer, run *Run) Request {
			records, _ := d.Store.Artifacts(run.ID)
			_ = d.Store.SetArtifacts(run.ID, records[:len(records)-1])
			return request(run)
		}, "does not match persisted run state"},

		{"no database", func(_ *Deployer, run *Run) Request {
			asked := request(run)
			asked.MongoURI = ""
			return asked
		}, "MONGODB_URI is missing or malformed"},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			deployer, run := deployable(t)
			asked := c.break_(deployer, run)
			_, _, err := deployer.check(asked)
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("err = %v, want something about %q", err, c.want)
			}
			if strings.Contains(err.Error(), "hunter2") {
				t.Error("the refusal quoted the connection string back")
			}
		})
	}
}

func TestOneDeploymentPerProject(t *testing.T) {
	deployer, run := deployable(t)
	_, key, err := deployer.check(request(run))
	if err != nil {
		t.Fatal(err)
	}
	if err := deployer.reserve(run.ID, key); err != nil {
		t.Fatal(err)
	}
	if err := deployer.reserve(NewRunID(), key); err == nil {
		t.Fatal("two deployments of one project at once")
	}
	deployer.release(key)
	if err := deployer.reserve(NewRunID(), key); err != nil {
		t.Fatalf("the project was not released: %v", err)
	}
}

func TestADeployingRunElsewhereStillOwnsTheProject(t *testing.T) {
	deployer, run := deployable(t)
	_, key, _ := deployer.check(request(run))

	// A run left mid-deployment by a restart: nothing is reserved in memory,
	// but the store remembers.
	_, _ = deployer.Store.Transition(run.ID, StateBootstrapping, nil)
	if err := deployer.reserve(NewRunID(), key); err == nil {
		t.Fatal("a run that survived a restart still owns its project")
	}
}

func TestCheckMongoURI(t *testing.T) {
	good := []string{
		"mongodb+srv://user:pass@cluster.mongodb.net/shop",
		"mongodb://127.0.0.1:27017/shop",
	}
	for _, value := range good {
		if err := CheckMongoURI(value); err != nil {
			t.Errorf("CheckMongoURI(%q) = %v", value, err)
		}
	}
	bad := []string{"", "postgres://host/db", "mongodb://", "mongodb+srv://user:p ss@host/db",
		"just a string"}
	for _, value := range bad {
		if err := CheckMongoURI(value); err == nil {
			t.Errorf("CheckMongoURI(%q) was accepted", value)
		}
	}
}

func TestCancelAndTeardownRules(t *testing.T) {
	deployer, run := deployable(t)

	if _, err := deployer.Cancel(context.Background(), NewRunID()); err == nil {
		t.Error("a run that does not exist cannot be cancelled")
	}

	result, err := deployer.Cancel(context.Background(), run.ID)
	if err != nil {
		t.Fatal(err)
	}
	if result["state"] != string(StateCancelled) {
		t.Errorf("result = %+v", result)
	}
	if _, err := deployer.Cancel(context.Background(), run.ID); err == nil {
		t.Error("a cancelled run is not still running")
	}

	// Nothing was deployed, so teardown has nothing to delete and says so.
	torn, err := deployer.Teardown(context.Background(), run.ID, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	if _, accepted := torn["accepted"]; accepted {
		t.Errorf("there was nothing to tear down: %+v", torn)
	}
	after, _ := deployer.Store.GetRun(run.ID)
	if after.State != StateDestroyed {
		t.Errorf("state = %s", after.State)
	}
	if _, err := deployer.Teardown(context.Background(), run.ID, nil, false); err == nil {
		t.Error("a destroyed run cannot be destroyed again")
	}
}

func TestARunningDeploymentIsNotTornDownByAccident(t *testing.T) {
	deployer, run := deployable(t)
	_, _ = deployer.Store.Transition(run.ID, StateBootstrapping, nil)
	_, _ = deployer.Store.Update(run.ID, map[string]any{
		"repo": map[string]any{"region": DefaultRegion},
	})

	if _, err := deployer.Teardown(context.Background(), run.ID, nil, false); err == nil {
		t.Fatal("a running deployment was torn down without being told twice")
	} else if !strings.Contains(err.Error(), "Cancel") {
		t.Errorf("err = %v", err)
	}
}

func TestRuntimeSecretCarriesTheWholeEnvironment(t *testing.T) {
	values := runtimeSecretValues("mongodb+srv://user:pass@cluster/db", map[string]string{
		"ApplicationUrl": "https://shop.example.com/",
	})
	if values["MONGODB_URI"] == "" || len(values["BETTER_AUTH_SECRET"]) < 32 {
		t.Errorf("values = %v", sortedKeys(values))
	}
	for _, name := range DeployerInjected {
		if values[name] != "https://shop.example.com" {
			t.Errorf("%s = %q", name, values[name])
		}
	}

	// With no address yet, the deployer-injected names are simply absent.
	early := runtimeSecretValues("mongodb://host/db", map[string]string{})
	if _, present := early["BETTER_AUTH_URL"]; present {
		t.Error("an address that is not known yet is not written")
	}
}

func TestEnvironmentFilesAreNeverCommitted(t *testing.T) {
	leaked := environmentFiles([]string{
		"app/page.tsx", ".env", "apps/web/.env.local", ".env.example", ".github/workflows/ci.yml",
	})
	if strings.Join(leaked, ",") != ".env,apps/web/.env.local" {
		t.Errorf("leaked = %v", leaked)
	}
}

func TestRepositoryName(t *testing.T) {
	cases := map[string]string{
		"https://github.com/acme/shop.git": "acme/shop",
		"git@github.com:acme/shop.git":     "acme/shop",
		"https://github.com/acme/shop":     "acme/shop",
		"https://gitlab.com/acme/shop.git": "",
		"":                                 "",
	}
	for remote, want := range cases {
		if got := RepositoryName(remote); got != want {
			t.Errorf("RepositoryName(%q) = %q, want %q", remote, got, want)
		}
	}
}

func TestOIDCSubjectsPinTheRepositoryAndBranch(t *testing.T) {
	identity := RepoIdentity{ID: 42}
	identity.Owner.ID = 7
	got := OIDCSubjects("acme/shop", "main", identity)
	want := []string{
		"repo:acme/shop:ref:refs/heads/main",
		"repo:acme@7/shop@42:ref:refs/heads/main",
	}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Errorf("subjects = %v", got)
	}
	// A repository whose ids are unknown still gets the name-based subject.
	if only := OIDCSubjects("acme/shop", "main", RepoIdentity{}); len(only) != 1 {
		t.Errorf("subjects = %v", only)
	}
}

func TestSelectRun(t *testing.T) {
	runs := []WorkflowRun{
		{URL: "https://gh/3", HeadSHA: "ccc", Status: "in_progress"},
		{URL: "https://gh/2", HeadSHA: "bbb", Status: "completed"},
		{URL: "https://gh/1", HeadSHA: "aaa", Status: "completed"},
	}
	if got, ok := SelectRun(runs, "bbb", ""); !ok || got.URL != "https://gh/2" {
		t.Errorf("the run for this commit = %+v", got)
	}
	// A commit that produced no run of its own is not somebody else's run.
	if _, ok := SelectRun(runs, "zzz", ""); ok {
		t.Error("a run was claimed that does not belong to this deployment")
	}
	// With no commit to go on, anything newer than what was already there.
	if got, ok := SelectRun(runs, "", "https://gh/3"); !ok || got.URL != "https://gh/2" {
		t.Errorf("newest new run = %+v", got)
	}
	if _, ok := SelectRun(nil, "", ""); ok {
		t.Error("no runs, no answer")
	}
}
