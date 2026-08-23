package srs

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http/httptest"
	"strings"
	"testing"
)

// call runs one request through the SRS handler and decodes the answer.
func call(t *testing.T, svc *Service, method, path string, body any) (int, map[string]any) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = strings.NewReader(string(raw))
	}
	r := httptest.NewRequest(method, path, reader)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	svc.Handler().ServeHTTP(w, r)

	out := map[string]any{}
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	return w.Code, out
}

func TestServiceCreatesAndListsProjects(t *testing.T) {
	svc := testService(t)

	status, body := call(t, svc, "POST", "/projects", map[string]any{
		"idea": "a corner shop till", "language": "English"})
	if status != 200 {
		t.Fatalf("create = %d %v", status, body)
	}
	project, _ := body["project"].(map[string]any)
	id := firstText(project["id"])
	if id == "" || !strings.HasPrefix(id, "prj_") {
		t.Fatalf("project = %v", project)
	}
	if project["status"] != StatusIntake {
		t.Errorf("status = %v", project["status"])
	}

	if status, body := call(t, svc, "POST", "/projects", map[string]any{"idea": "  "}); status != 400 {
		t.Errorf("an empty idea = %d %v", status, body)
	}

	status, body = call(t, svc, "GET", "/projects", nil)
	if status != 200 || len(body["projects"].([]any)) != 1 {
		t.Errorf("list = %d %v", status, body)
	}

	status, body = call(t, svc, "GET", "/projects/"+id, nil)
	if status != 200 {
		t.Fatalf("detail = %d %v", status, body)
	}
	if body["srs"] != nil || body["summary"] != nil {
		t.Error("nothing has been specified yet")
	}

	if status, _ := call(t, svc, "GET", "/projects/prj_nothere", nil); status != 404 {
		t.Errorf("a missing project = %d", status)
	}
}

func TestServiceRefusesTheUnwritten(t *testing.T) {
	svc := testService(t)
	_, body := call(t, svc, "POST", "/projects", map[string]any{"idea": "a shop"})
	id := firstText(body["project"].(map[string]any)["id"])

	for _, path := range []string{
		"/srs-json", "/requirements", "/ambiguities", "/risks",
		"/builder-handoff", "/builder-prompt", "/download/json", "/download/pdf",
	} {
		status, out := call(t, svc, "GET", "/projects/"+id+path, nil)
		if status != 404 {
			t.Errorf("%s = %d %v", path, status, out)
		}
		if detail := firstText(out["detail"]); detail == "" && path != "/builder-prompt" {
			t.Errorf("%s gave no reason", path)
		}
	}
	if status, _ := call(t, svc, "POST", "/projects/"+id+"/customize",
		map[string]any{"prompt": "add a wishlist"}); status != 400 {
		t.Error("there is nothing to customize yet")
	}
}

func TestServiceInterviewAndPlan(t *testing.T) {
	svc := testService(t)
	ctx := context.Background()

	_, body := call(t, svc, "POST", "/projects", map[string]any{
		"idea": "a hotel booking site where guests reserve rooms online"})
	id := firstText(body["project"].(map[string]any)["id"])

	status, body := call(t, svc, "POST", "/projects/"+id+"/analyze", nil)
	if status != 200 {
		t.Fatalf("analyze = %d %v", status, body)
	}
	classification, _ := body["classification"].(map[string]any)
	if firstText(classification["domain_key"]) != "hotel" {
		t.Errorf("classification = %v", classification)
	}
	question, _ := body["question"].(map[string]any)
	if firstText(question["question"]) == "" {
		t.Fatalf("no first question: %v", body)
	}

	// The interview picks up where analyze left off.
	status, body = call(t, svc, "GET", "/projects/"+id+"/interview", nil)
	if status != 200 || body["question"] == nil {
		t.Fatalf("interview = %d %v", status, body)
	}
	first := body["question"].(map[string]any)
	if firstText(first["id"]) != firstText(question["id"]) {
		t.Errorf("the interview moved on by itself: %v vs %v", first["id"], question["id"])
	}

	// Answering the app-type question moves on to the next one.
	status, body = call(t, svc, "POST", "/projects/"+id+"/interview/answer",
		map[string]any{"key": firstText(first["key"]), "value": "saas"})
	if status != 200 {
		t.Fatalf("answer = %d %v", status, body)
	}
	if body["done"] == true {
		t.Error("one answer is not the whole interview")
	}

	session, err := svc.Repo.GetSession(ctx, id)
	if err != nil || session == nil {
		t.Fatalf("session = %v %v", session, err)
	}
	if _, ok := session.Answers[firstText(first["key"])]; !ok {
		t.Errorf("the answer was not recorded: %v", session.Answers)
	}

	// A plan can be written from a part-finished interview.
	status, body = call(t, svc, "POST", "/projects/"+id+"/plan", map[string]any{})
	if status != 200 {
		t.Fatalf("plan = %d %v", status, body)
	}
	for _, key := range []string{"plan", "markdown", "version", "versions",
		"content_hash", "can_approve", "reason", "open_questions"} {
		if _, ok := body[key]; !ok {
			t.Errorf("the plan envelope lost %q", key)
		}
	}
	if body["version"].(float64) != 1 {
		t.Errorf("version = %v", body["version"])
	}

	status, approval := call(t, svc, "POST", "/projects/"+id+"/plan/approve", map[string]any{})
	if status != 200 || approval["approved"] != true {
		t.Fatalf("approve = %d %v", status, approval)
	}
	// Approving twice is refused rather than silently repeated.
	if status, _ := call(t, svc, "POST", "/projects/"+id+"/plan/approve", map[string]any{}); status != 409 {
		t.Errorf("second approval = %d", status)
	}
}

func TestServiceGeneratesAndReadsBack(t *testing.T) {
	svc := testService(t)
	_, body := call(t, svc, "POST", "/projects", map[string]any{"idea": "a corner shop till"})
	id := firstText(body["project"].(map[string]any)["id"])
	call(t, svc, "POST", "/projects/"+id+"/analyze", nil)
	call(t, svc, "POST", "/projects/"+id+"/plan", map[string]any{})
	call(t, svc, "POST", "/projects/"+id+"/plan/approve", map[string]any{})

	status, body := call(t, svc, "POST", "/projects/"+id+"/generate-srs", nil)
	if status != 200 {
		t.Fatalf("generate = %d %v", status, body)
	}
	if body["version"] != "1.0.0" {
		t.Errorf("version = %v", body["version"])
	}
	summary, _ := body["summary"].(map[string]any)
	if summary["functional"].(float64) < 3 {
		t.Errorf("summary = %v", summary)
	}

	status, body = call(t, svc, "GET", "/projects/"+id+"/srs-json", nil)
	if status != 200 || body["srs_document"] == nil {
		t.Fatalf("srs-json = %d", status)
	}

	status, body = call(t, svc, "GET", "/projects/"+id+"/requirements", nil)
	if status != 200 || len(body["functional_requirements"].([]any)) == 0 {
		t.Fatalf("requirements = %d %v", status, body)
	}

	status, body = call(t, svc, "GET", "/projects/"+id+"/diagrams", nil)
	if status != 200 {
		t.Fatalf("diagrams = %d", status)
	}
	diagrams, _ := body["diagrams"].([]any)
	if len(diagrams) != 11 {
		t.Fatalf("diagrams = %d", len(diagrams))
	}
	inlined := 0
	for _, raw := range diagrams {
		if firstText(raw.(map[string]any)["svg"]) != "" {
			inlined++
		}
	}
	if inlined != 11 {
		t.Errorf("%d of 11 diagrams came back with their drawing", inlined)
	}

	status, handoff := call(t, svc, "GET", "/projects/"+id+"/builder-handoff", nil)
	if status != 200 {
		t.Fatalf("handoff = %d %v", status, handoff)
	}
	if handoff["handoff_version"].(float64) != 5 {
		t.Errorf("handoff_version = %v", handoff["handoff_version"])
	}
	if firstText(handoff["prompt"]) == "" {
		t.Error("the build contract is empty")
	}

	// The prompt endpoint is plain text, because it is pasted straight in.
	r := httptest.NewRequest("GET", "/projects/"+id+"/builder-prompt", nil)
	w := httptest.NewRecorder()
	svc.Handler().ServeHTTP(w, r)
	if w.Code != 200 || !strings.HasPrefix(w.Body.String(), "AGENTFORGE BUILD HANDOFF") {
		t.Errorf("builder-prompt = %d %q", w.Code, truncate(w.Body.String(), 80))
	}

	// The whole document downloads, and so does the PDF.
	r = httptest.NewRequest("GET", "/projects/"+id+"/download/pdf", nil)
	w = httptest.NewRecorder()
	svc.Handler().ServeHTTP(w, r)
	if w.Code != 200 || w.Header().Get("Content-Type") != "application/pdf" {
		t.Fatalf("pdf = %d %q", w.Code, w.Header().Get("Content-Type"))
	}
	if !strings.HasPrefix(w.Body.String(), "%PDF-") || w.Body.Len() < 20000 {
		t.Errorf("the PDF is %d bytes", w.Body.Len())
	}
	if !strings.Contains(w.Header().Get("Content-Disposition"), "_SRS_v1.0.0.pdf") {
		t.Errorf("disposition = %q", w.Header().Get("Content-Disposition"))
	}

	// An edit writes a new version and says what changed.
	status, body = call(t, svc, "POST", "/projects/"+id+"/customize",
		map[string]any{"prompt": "add a loyalty points programme"})
	if status != 200 {
		t.Fatalf("customize = %d %v", status, body)
	}
	if body["version"] != "1.1.0" {
		t.Errorf("version = %v", body["version"])
	}
	if len(body["diff_summary"].([]any)) == 0 {
		t.Error("an edit must report what it changed")
	}
}

func TestServiceIngestsAnAttachment(t *testing.T) {
	svc := testService(t)
	_, body := call(t, svc, "POST", "/projects", map[string]any{"idea": "a cafe site"})
	id := firstText(body["project"].(map[string]any)["id"])

	status, body := call(t, svc, "POST", "/projects/"+id+"/inputs-json", map[string]any{
		"mode": "text", "text": "we open at seven"})
	if status != 200 || body["source"] == nil {
		t.Fatalf("text input = %d %v", status, body)
	}

	// An image is copied where the built app will serve it.
	png := base64.StdEncoding.EncodeToString([]byte("\x89PNG\r\n\x1a\n not really a png"))
	status, body = call(t, svc, "POST", "/projects/"+id+"/inputs-json", map[string]any{
		"mode": "image", "filename": "our logo!.png", "content_type": "image/png",
		"purpose": "our logo", "data_base64": png})
	if status != 200 {
		t.Fatalf("image input = %d %v", status, body)
	}
	if firstText(body["url"]) != "/uploads/our-logo.png" {
		t.Errorf("url = %v", body["url"])
	}
	if firstText(body["purpose"]) != "our logo" {
		t.Errorf("purpose = %v", body["purpose"])
	}

	// Both reach the brief, in the order they were given.
	project, _ := svc.Repo.GetProject(context.Background(), id)
	brief := svc.Brief(context.Background(), project)
	if !strings.Contains(brief, "we open at seven") {
		t.Errorf("brief = %q", brief)
	}
	if !strings.Contains(brief, "`/uploads/our-logo.png`") {
		t.Errorf("brief = %q", brief)
	}

	if status, _ := call(t, svc, "POST", "/projects/"+id+"/inputs-json", map[string]any{
		"data_base64": "!!!not base64!!!"}); status != 400 {
		t.Error("bad base64 must be refused")
	}
	if status, _ := call(t, svc, "POST", "/projects/prj_nothere/inputs-json",
		map[string]any{"text": "hello"}); status != 404 {
		t.Error("a missing project must be refused")
	}
}

func TestServiceDiscard(t *testing.T) {
	svc := testService(t)
	ctx := context.Background()
	_, body := call(t, svc, "POST", "/projects", map[string]any{"idea": "a shop"})
	id := firstText(body["project"].(map[string]any)["id"])

	status, body := call(t, svc, "POST", "/projects/"+id+"/discard", nil)
	if status != 200 || body["discarded"] != true {
		t.Fatalf("discard = %d %v", status, body)
	}

	// An approved specification is a record, not a draft.
	_ = svc.Repo.UpdateProject(ctx, id, Doc{"status": StatusApproved})
	if status, _ := call(t, svc, "POST", "/projects/"+id+"/discard", nil); status != 409 {
		t.Errorf("discarding an approved SRS = %d", status)
	}
}

func TestServiceConsole(t *testing.T) {
	svc := testService(t)
	_, body := call(t, svc, "POST", "/projects", map[string]any{"idea": "a shop"})
	id := firstText(body["project"].(map[string]any)["id"])

	status, body := call(t, svc, "GET", "/projects/"+id+"/events", nil)
	if status != 200 {
		t.Fatalf("events = %d", status)
	}
	events, _ := body["events"].([]any)
	if len(events) == 0 {
		t.Error("creating a project should say so on the console")
	}
	for _, path := range []string{"/traces", "/errors"} {
		if status, _ := call(t, svc, "GET", "/projects/"+id+path, nil); status != 200 {
			t.Errorf("%s = %d", path, status)
		}
	}
	if status, body := call(t, svc, "GET", "/health", nil); status != 200 || body["ok"] != true {
		t.Errorf("health = %d %v", status, body)
	}
}

func TestPublicName(t *testing.T) {
	for in, want := range map[string]string{
		"our logo!.png": "our-logo.png", "../../etc/passwd": "passwd",
		"": "upload", "!!!": "upload", "a b/c d.jpg": "c-d.jpg",
	} {
		if got := publicName(in); got != want {
			t.Errorf("publicName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestBumpMinor(t *testing.T) {
	for in, want := range map[string]string{
		"1.0.0": "1.1.0", "1.9.0": "1.10.0", "2.0.3": "2.1.0",
		"": "1.1.0", "abc": "1.1.0",
	} {
		if got := bumpMinor(in); got != want {
			t.Errorf("bumpMinor(%q) = %q, want %q", in, got, want)
		}
	}
}

// The specification is composed from the approved plan and nothing else — the
// rule the whole product rests on. It was enforced nowhere in the API: a
// project with no plan and no interview at all was given a full SRS.
func TestASpecificationIsOnlyWrittenFromAnApprovedPlan(t *testing.T) {
	svc := testService(t)
	ctx := context.Background()

	status, body := call(t, svc, "POST", "/projects", map[string]any{
		"idea": "a corner shop till", "language": "English"})
	if status != 200 {
		t.Fatalf("create = %d %v", status, body)
	}
	id, _ := body["id"].(string)
	if id == "" {
		project, _ := body["project"].(map[string]any)
		id, _ = project["id"].(string)
	}
	if id == "" {
		t.Fatalf("no project id in %v", body)
	}

	// Nothing has been planned, so there is nothing to compose from.
	if status, body := call(t, svc, "POST", "/projects/"+id+"/generate-srs", nil); status < 400 {
		t.Fatalf("a project with no plan was given a specification: %d %v", status, body)
	}

	// A plan the customer has not agreed to is not an approved plan.
	if _, err := svc.Repo.SavePlan(ctx, PlanRecordDoc{
		ProjectID: id, Version: 1, Plan: Plan{AppName: "Till"}}); err != nil {
		t.Fatal(err)
	}
	status, body = call(t, svc, "POST", "/projects/"+id+"/generate-srs", nil)
	if status != 409 {
		t.Fatalf("an unapproved plan was accepted: %d %v", status, body)
	}
}

// The Discard button says it throws the specification away. It reset the
// project and left every endpoint serving the whole document.
func TestDiscardingASpecificationRemovesIt(t *testing.T) {
	svc := testService(t)
	ctx := context.Background()

	status, body := call(t, svc, "POST", "/projects", map[string]any{
		"idea": "a private clinic's notes", "language": "English"})
	if status != 200 {
		t.Fatalf("create = %d %v", status, body)
	}
	id, _ := body["id"].(string)
	if id == "" {
		project, _ := body["project"].(map[string]any)
		id, _ = project["id"].(string)
	}

	if _, err := svc.Repo.SaveVersion(ctx, Version{
		ProjectID: id, Version: "1.0.0",
		SRS: Envelope{Document: Document{DocumentTitle: "Clinic", Version: "1.0.0"}},
	}); err != nil {
		t.Fatal(err)
	}
	if err := svc.Repo.SaveDiagrams(ctx, id, []Diagram{{Kind: "context", Title: "Context"}}); err != nil {
		t.Fatal(err)
	}
	if status, _ := call(t, svc, "GET", "/projects/"+id+"/srs-json", nil); status != 200 {
		t.Fatalf("the fixture did not take: srs-json = %d", status)
	}

	if status, body := call(t, svc, "POST", "/projects/"+id+"/discard", nil); status != 200 {
		t.Fatalf("discard = %d %v", status, body)
	}

	// It is gone, not merely hidden.
	for _, path := range []string{"/srs-json", "/requirements", "/diagrams"} {
		if status, body := call(t, svc, "GET", "/projects/"+id+path, nil); status < 400 {
			t.Errorf("%s still serves a discarded specification: %d %v", path, status, body)
		}
	}
	versions, err := svc.Repo.ListVersions(ctx, id)
	if err != nil || len(versions) != 0 {
		t.Errorf("versions = %d %v", len(versions), err)
	}
}
