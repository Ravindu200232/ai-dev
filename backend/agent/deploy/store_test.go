package deploy

import (
	"strings"
	"testing"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return store
}

func TestRunRoundTrip(t *testing.T) {
	store := testStore(t)
	id := NewRunID()
	if !strings.HasPrefix(id, "run_") || len(id) != 28 {
		t.Fatalf("run id = %q", id)
	}

	run, err := store.CreateRun(id, "corner-shop", "/src", "/staged")
	if err != nil {
		t.Fatal(err)
	}
	if run.State != StateDraft || run.CreatedAt == "" {
		t.Fatalf("run = %+v", run)
	}

	got, err := store.GetRun(id)
	if err != nil || got == nil {
		t.Fatalf("GetRun = %v %v", got, err)
	}
	if got.ProjectName != "corner-shop" || got.StagedPath != "/staged" {
		t.Errorf("round trip lost fields: %+v", got)
	}
	if got.Spec == nil || got.Plan == nil {
		t.Error("the json sections must read back as objects, not null")
	}

	if missing, err := store.GetRun("run_nothere"); err != nil || missing != nil {
		t.Errorf("a run that does not exist is nil, not an error: %v %v", missing, err)
	}
}

func TestStateMachineRefusesASkippedStep(t *testing.T) {
	store := testStore(t)
	id := NewRunID()
	if _, err := store.CreateRun(id, "shop", "/src", "/staged"); err != nil {
		t.Fatal(err)
	}

	if _, err := store.Transition(id, StateLive, nil); err == nil {
		t.Fatal("DRAFT must not jump straight to LIVE")
	}
	for _, step := range []State{StateAnalyzing, StateReviewReady, StateBootstrapping,
		StateCIRunning, StateDeploying, StateValidating, StateLive} {
		if _, err := store.Transition(id, step, nil); err != nil {
			t.Fatalf("%s: %v", step, err)
		}
	}
	// A stage that re-reports where it already is is not an error.
	if _, err := store.Transition(id, StateLive, nil); err != nil {
		t.Errorf("staying put: %v", err)
	}
	// A destroyed run is finished.
	if _, err := store.Transition(id, StateDestroyed, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Transition(id, StateAnalyzing, nil); err == nil {
		t.Error("nothing follows DESTROYED")
	}

	run, _ := store.GetRun(id)
	if run.State != StateDestroyed {
		t.Errorf("state = %q", run.State)
	}
}

func TestUpdateOnlyTouchesWhatItIsGiven(t *testing.T) {
	store := testStore(t)
	id := NewRunID()
	_, _ = store.CreateRun(id, "shop", "/src", "/staged")

	run, err := store.Update(id, map[string]any{
		"plan":    Plan{ProjectSlug: "corner-shop", Region: "ap-south-1", Port: 3000},
		"error":   "nothing went wrong",
		"unknown": "ignored",
	})
	if err != nil {
		t.Fatal(err)
	}
	if run.Plan["project_slug"] != "corner-shop" {
		t.Errorf("a typed value should store as its JSON form: %v", run.Plan)
	}
	if run.ProjectName != "shop" || run.StagedPath != "/staged" {
		t.Error("an update must not clear what it was not given")
	}
	if run.Error != "nothing went wrong" {
		t.Errorf("error = %q", run.Error)
	}
	if run.UpdatedAt == run.CreatedAt {
		t.Error("a write should move updated_at")
	}
}

func TestStoreRedactsBeforeItWrites(t *testing.T) {
	store := testStore(t)
	id := NewRunID()
	_, _ = store.CreateRun(id, "shop", "/src", "/staged")

	_, err := store.Update(id, map[string]any{
		"spec": map[string]any{
			"MONGODB_URI": "mongodb+srv://user:hunter2@cluster0.example.net/db",
			"safe":        "this is fine",
			"nested":      map[string]any{"GITHUB_TOKEN": "ghp_abcdefghijklmnopqrstuvwxyz012345"},
			"free_text":   "the key is AKIA1234567890ABCDEF, do not share it",
			"env_file":    "PORT=3000\nAUTH_SECRET=hunter2\n",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	run, _ := store.GetRun(id)
	blob := SafeJSON(run.Spec)

	for _, leak := range []string{"hunter2", "ghp_abcdefghijklmnopqrstuvwxyz012345", "AKIA1234567890ABCDEF"} {
		if strings.Contains(blob, leak) {
			t.Errorf("a secret reached the store: %q in %s", leak, blob)
		}
	}
	if !strings.Contains(blob, "this is fine") {
		t.Errorf("redaction ate the ordinary data: %s", blob)
	}
	if !strings.Contains(blob, "PORT=3000") {
		t.Errorf("a variable that is not a secret should survive: %s", blob)
	}
}

func TestEvents(t *testing.T) {
	store := testStore(t)
	id := NewRunID()
	_, _ = store.CreateRun(id, "shop", "/src", "/staged")

	for i, message := range []string{"reading the project", "planning", "deploying"} {
		if err := store.AddEvent(id, Event{
			Stage: "stage" + itoa(i), Level: "info", Message: message,
		}); err != nil {
			t.Fatal(err)
		}
	}
	events, err := store.Events(id, 0, 0)
	if err != nil || len(events) != 3 {
		t.Fatalf("events = %d %v", len(events), err)
	}
	if events[0].EventID != 1 || events[2].EventID != 3 {
		t.Errorf("ids must be stable and ordered: %+v", events)
	}
	if events[0].CreatedAt == "" {
		t.Error("an event is stamped when it is written")
	}

	// Polling from the last id returns only what is new.
	since, _ := store.Events(id, 2, 0)
	if len(since) != 1 || since[0].Message != "deploying" {
		t.Errorf("since = %+v", since)
	}
	if empty, _ := store.Events("run_nothere", 0, 0); len(empty) != 0 {
		t.Error("a run with no log has no events, not an error")
	}

	_ = store.AddEvent(id, Event{Message: "connecting to mongodb+srv://u:p@host/db"})
	events, _ = store.Events(id, 3, 0)
	if strings.Contains(events[0].Message, "hunter") || strings.Contains(events[0].Message, ":p@") {
		t.Errorf("the console leaked a connection string: %q", events[0].Message)
	}
}

func TestArtifactsAndEvidence(t *testing.T) {
	store := testStore(t)
	id := NewRunID()
	_, _ = store.CreateRun(id, "shop", "/src", "/staged")

	if err := store.SetArtifacts(id, []Artifact{
		{Path: "Dockerfile", Kind: "docker", SHA256: "b", Size: 20},
		{Path: ".github/workflows/deploy.yml", Kind: "ci", SHA256: "a", Size: 10, OriginalExists: true},
	}); err != nil {
		t.Fatal(err)
	}
	got, err := store.Artifacts(id)
	if err != nil || len(got) != 2 {
		t.Fatalf("artifacts = %+v %v", got, err)
	}
	if got[0].Path != ".github/workflows/deploy.yml" {
		t.Errorf("artifacts should come back in path order: %+v", got)
	}
	if !got[0].OriginalExists {
		t.Error("whether we overwrote something is the point of the record")
	}

	// Writing them again replaces rather than appends.
	_ = store.SetArtifacts(id, []Artifact{{Path: "Dockerfile"}})
	if got, _ := store.Artifacts(id); len(got) != 1 {
		t.Errorf("artifacts = %d", len(got))
	}

	for _, name := range []string{"health check", "screenshot"} {
		if err := store.AddEvidence(id, name, "/runs/"+id+"/"+name); err != nil {
			t.Fatal(err)
		}
	}
	rows, _ := store.Evidence(id)
	if len(rows) != 2 || rows[1].ID != 2 {
		t.Fatalf("evidence = %+v", rows)
	}
	if rows[0].Verified {
		t.Error("nothing is verified until somebody says so")
	}
	if err := store.VerifyEvidence(id, 1, true); err != nil {
		t.Fatal(err)
	}
	rows, _ = store.Evidence(id)
	if !rows[0].Verified || rows[1].Verified {
		t.Errorf("evidence = %+v", rows)
	}
}

func TestListRunsNewestFirst(t *testing.T) {
	store := testStore(t)
	var ids []string
	for i := 0; i < 3; i++ {
		id := NewRunID()
		ids = append(ids, id)
		if _, err := store.CreateRun(id, "shop"+itoa(i), "/src", "/staged"); err != nil {
			t.Fatal(err)
		}
	}
	// Touching the first one moves it to the top.
	if _, err := store.Update(ids[0], map[string]any{"error": "touched"}); err != nil {
		t.Fatal(err)
	}
	runs, err := store.ListRuns(0)
	if err != nil || len(runs) != 3 {
		t.Fatalf("runs = %d %v", len(runs), err)
	}
	if runs[0].ID != ids[0] {
		t.Errorf("the most recently touched run comes first: %q", runs[0].ID)
	}
	if limited, _ := store.ListRuns(2); len(limited) != 2 {
		t.Error("the limit is not applied")
	}
}

func TestRedaction(t *testing.T) {
	if !IsSecretName("AUTH_SECRET") || !IsSecretName("mongodb_uri") {
		t.Error("a name that says secret is a secret")
	}
	if IsSecretName("PORT") {
		t.Error("a port is not a secret")
	}
	cases := map[string]string{
		"mongodb+srv://u:p@host/db":            redacted,
		"AKIA1234567890ABCDEF":                 redacted,
		"ghp_abcdefghijklmnopqrstuvwxyz012345": redacted,
		"https://user:pass@github.com/x/y.git": redacted,
		"just some prose":                      "just some prose",
	}
	for in, want := range cases {
		got := RedactText(in)
		if want == redacted && !strings.Contains(got, redacted) {
			t.Errorf("RedactText(%q) = %q, want it redacted", in, got)
		}
		if want != redacted && got != want {
			t.Errorf("RedactText(%q) = %q", in, got)
		}
	}
	if got := RedactText("AUTH_SECRET=hunter2"); strings.Contains(got, "hunter2") {
		t.Errorf("an assignment must be redacted: %q", got)
	}
	if got := SHA256([]byte("abc")); got != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" {
		t.Errorf("SHA256 = %q", got)
	}
	if SHA256File("/nowhere/at/all") != "" {
		t.Error("a file that is not there has no hash")
	}
}
