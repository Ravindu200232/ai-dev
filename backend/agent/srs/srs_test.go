package srs

import (
	"context"
	"strings"
	"testing"
)

// Every embedded catalog has to parse, and the counts are pinned so a bad
// re-extraction is caught here rather than halfway through an interview.
func TestKnowledgeLoads(t *testing.T) {
	k := Knowledge()

	if len(k.Domains) != 7 {
		t.Errorf("domains = %d, want 7: %v", len(k.Domains), keysOf(k.Domains))
	}
	for _, want := range []string{"hotel", "hospital", "retail", "school", "restaurant", "vehicle", "custom"} {
		if _, ok := k.Domains[want]; !ok {
			t.Errorf("the %q domain is missing", want)
		}
	}
	if len(k.AppTypes) != 9 {
		t.Errorf("app types = %d, want 9", len(k.AppTypes))
	}
	if len(k.Topics) != 41 {
		t.Errorf("interview topics = %d, want 41", len(k.Topics))
	}
	if len(k.Signals) != 19 {
		t.Errorf("coverage areas = %d, want 19", len(k.Signals))
	}
	if len(k.Standards.DiagramNotation) != 11 {
		t.Errorf("diagram kinds = %d, want 11", len(k.Standards.DiagramNotation))
	}
	if k.Standards.SRSStandard != "ISO/IEC/IEEE 29148:2018" {
		t.Errorf("srs standard = %q", k.Standards.SRSStandard)
	}

	// A domain is only useful if it brought its tables and roles with it.
	hotel := GetDomain("hotel")
	if len(hotel.Tables) == 0 || len(hotel.Roles) == 0 {
		t.Errorf("the hotel domain came through empty: %d tables, %d roles",
			len(hotel.Tables), len(hotel.Roles))
	}
	if hotel.Tables[0].TableName == "" || len(hotel.Tables[0].Fields) == 0 {
		t.Errorf("hotel tables lost their shape: %+v", hotel.Tables[0])
	}
}

// The predicate expressions are resolved by name in interview.go, so every one
// the catalog names must be one the registry knows.
func TestEveryTopicPredicateIsKnown(t *testing.T) {
	seen := map[string]int{}
	for _, topic := range Knowledge().Topics {
		for _, expr := range []string{topic.AppliesTo, topic.OptionsFrom, topic.RepeatsOver} {
			if expr != "" {
				seen[expr]++
			}
		}
	}
	if len(seen) == 0 {
		t.Fatal("no predicates were carried through the extraction")
	}
	// 19 distinct expressions across applies_to, options_from and repeats_over.
	if len(seen) != 19 {
		t.Errorf("distinct predicates = %d, want 19: %v", len(seen), keysOf(seen))
	}
}

func TestClassifyDomain(t *testing.T) {
	cases := map[string]string{
		"a hotel booking system with rooms and guest check-in": "hotel",
		"a clinic app for patients, doctors and appointments":  "hospital",
		"a school portal for students, teachers and grades":    "school",
		"something entirely unlike anything":                   GenericDomain,
	}
	for idea, want := range cases {
		got, confidence := ClassifyDomain(idea)
		if got != want {
			t.Errorf("ClassifyDomain(%q) = %q, want %q", idea, got, want)
		}
		if confidence <= 0 || confidence > 0.97 {
			t.Errorf("confidence for %q is %v, outside the sane range", idea, confidence)
		}
	}
}

func TestGuessAppType(t *testing.T) {
	got, confidence, _ := GuessAppType("a point of sale till for a shop counter")
	if got != "pos" {
		t.Errorf("GuessAppType = %q, want pos", got)
	}
	if confidence < Knowledge().GuessFloor {
		t.Errorf("a clear match should clear the floor: %v < %v", confidence, Knowledge().GuessFloor)
	}
	// Nothing to go on must not produce a confident guess.
	if _, low, _ := GuessAppType("zzz"); low != 0 {
		t.Errorf("an unmatched idea should score 0, got %v", low)
	}
	if options := AppTypeOptions(); len(options) != 9 {
		t.Errorf("app type options = %d, want 9", len(options))
	}
}

func TestCoverageCovered(t *testing.T) {
	if !CoverageCovered("users_roles", "there are admins and staff who log in") {
		t.Error("a brief naming roles should cover users_roles")
	}
	if CoverageCovered("payments_billing", "a simple notes app") {
		t.Error("a notes app does not cover payments")
	}
}

// --- document -------------------------------------------------------------------

func TestDocumentValidate(t *testing.T) {
	full := func() *Document {
		return &Document{
			ProjectName:               "Demo",
			FunctionalRequirements:    make([]Requirement, 3),
			NonFunctionalRequirements: make([]NonFunctional, 3),
			Roles:                     make([]Role, 1),
		}
	}
	if err := full().Validate(); err != nil {
		t.Fatalf("a complete document should validate: %v", err)
	}

	short := full()
	short.FunctionalRequirements = make([]Requirement, 2)
	if err := short.Validate(); err == nil || !strings.Contains(err.Error(), "functional_requirements") {
		t.Errorf("two functional requirements should be refused, got %v", err)
	}

	noRoles := full()
	noRoles.Roles = nil
	if err := noRoles.Validate(); err == nil {
		t.Error("a document with no roles should be refused")
	}

	unnamed := full()
	unnamed.ProjectName = "  "
	if err := unnamed.Validate(); err == nil {
		t.Error("a document with no project name should be refused")
	}
}

func TestSummarize(t *testing.T) {
	doc := &Document{
		FunctionalRequirements:    make([]Requirement, 7),
		NonFunctionalRequirements: make([]NonFunctional, 4),
		BusinessWorkflows:         make([]Workflow, 2),
		Roles:                     make([]Role, 3),
		MainModules:               []string{"a", "b"},
		Diagrams:                  make([]Diagram, 11),
		DatabaseDesign:            DatabaseDesign{Tables: make([]Table, 5)},
		Ambiguities: []Ambiguity{
			{NeedsClarification: true}, {NeedsClarification: false}, {NeedsClarification: true},
		},
	}
	got := Summarize(doc)
	want := Summary{Functional: 7, NonFunctional: 4, UseCases: 2, OpenAmbiguities: 2,
		Tables: 5, Roles: 3, Modules: 2, Diagrams: 11}
	if got != want {
		t.Errorf("Summarize = %+v, want %+v", got, want)
	}
}

func TestTitleFrom(t *testing.T) {
	cases := map[string]string{
		"A hotel booking app. It has rooms.": "A hotel booking app",
		"first line\nsecond line":            "first line",
		"   ":                                "Untitled project",
	}
	for in, want := range cases {
		if got := TitleFrom(in); got != want {
			t.Errorf("TitleFrom(%q) = %q, want %q", in, got, want)
		}
	}
	if got := TitleFrom(strings.Repeat("x", 200)); len(got) > 80 {
		t.Errorf("a long idea should be trimmed, got %d chars", len(got))
	}
}

func TestNewID(t *testing.T) {
	id := NewID("prj_")
	if !strings.HasPrefix(id, "prj_") || len(id) != len("prj_")+24 {
		t.Errorf("NewID = %q, want a prefix plus 24 hex characters", id)
	}
	if NewID("prj_") == id {
		t.Error("two ids should not collide")
	}
}

// --- store ------------------------------------------------------------------------

func TestMemoryStoreQuerySurface(t *testing.T) {
	ctx := context.Background()
	s := NewMemoryStore()

	for _, doc := range []Doc{
		{"id": "a", "project_id": "p1", "created_at": "2026-01-01", "version": 1},
		{"id": "b", "project_id": "p1", "created_at": "2026-01-03", "version": 3},
		{"id": "c", "project_id": "p2", "created_at": "2026-01-02", "version": 2},
	} {
		if err := s.InsertOne(ctx, CollPlans, doc); err != nil {
			t.Fatal(err)
		}
	}

	one, err := s.FindOne(ctx, CollPlans, Doc{"id": "b"})
	if err != nil || one == nil || one["project_id"] != "p1" {
		t.Fatalf("FindOne = %v, %v", one, err)
	}
	if missing, _ := s.FindOne(ctx, CollPlans, Doc{"id": "nope"}); missing != nil {
		t.Errorf("a miss should be nil, got %v", missing)
	}

	// Equality plus one ascending sort key — the whole query surface.
	asc, _ := s.Find(ctx, CollPlans, Doc{"project_id": "p1"}, "created_at", 1, 0)
	if len(asc) != 2 || asc[0]["id"] != "a" {
		t.Errorf("ascending find = %v", asc)
	}
	desc, _ := s.Find(ctx, CollPlans, Doc{"project_id": "p1"}, "created_at", -1, 0)
	if len(desc) != 2 || desc[0]["id"] != "b" {
		t.Errorf("descending find = %v", desc)
	}
	// Version sorts numerically, not as text.
	byVersion, _ := s.Find(ctx, CollPlans, nil, "version", -1, 1)
	if len(byVersion) != 1 || byVersion[0]["id"] != "b" {
		t.Errorf("numeric sort = %v", byVersion)
	}
	in, _ := s.Find(ctx, CollPlans, Doc{"id": Doc{"$in": []any{"a", "c"}}}, "id", 1, 0)
	if len(in) != 2 {
		t.Errorf("$in = %v", in)
	}

	changed, err := s.UpdateOne(ctx, CollPlans, Doc{"id": "a"}, Doc{"approved": true})
	if err != nil || !changed {
		t.Fatalf("UpdateOne = %v, %v", changed, err)
	}
	after, _ := s.FindOne(ctx, CollPlans, Doc{"id": "a"})
	if after["approved"] != true {
		t.Errorf("the update did not stick: %v", after)
	}

	if err := s.DeleteMany(ctx, CollPlans, Doc{"project_id": "p1"}); err != nil {
		t.Fatal(err)
	}
	left, _ := s.Find(ctx, CollPlans, nil, "id", 1, 0)
	if len(left) != 1 || left[0]["id"] != "c" {
		t.Errorf("after delete = %v", left)
	}
}

// A document handed back must not be the one that is stored.
func TestMemoryStoreDoesNotShareState(t *testing.T) {
	ctx := context.Background()
	s := NewMemoryStore()
	_ = s.InsertOne(ctx, CollProjects, Doc{"id": "x", "title": "before"})

	got, _ := s.FindOne(ctx, CollProjects, Doc{"id": "x"})
	got["title"] = "after"

	again, _ := s.FindOne(ctx, CollProjects, Doc{"id": "x"})
	if again["title"] != "before" {
		t.Errorf("mutating a returned document changed the store: %v", again)
	}
}

func TestRepoProjectRoundTrip(t *testing.T) {
	ctx := context.Background()
	repo := NewRepo(NewMemoryStore())

	p := NewProject("a hotel booking app", "English")
	if err := repo.CreateProject(ctx, p); err != nil {
		t.Fatal(err)
	}
	got, err := repo.GetProject(ctx, p.ID)
	if err != nil || got == nil {
		t.Fatalf("GetProject = %v, %v", got, err)
	}
	if got.Title != "a hotel booking app" || got.Status != StatusIntake {
		t.Errorf("round trip lost fields: %+v", got)
	}

	if err := repo.UpdateProject(ctx, p.ID, Doc{"status": StatusGenerated}); err != nil {
		t.Fatal(err)
	}
	after, _ := repo.GetProject(ctx, p.ID)
	if after.Status != StatusGenerated {
		t.Errorf("status = %q", after.Status)
	}
	if after.UpdatedAt == "" {
		t.Error("updated_at should be stamped on every write")
	}
	if missing, _ := repo.GetProject(ctx, "prj_nothere"); missing != nil {
		t.Error("a project that does not exist should be nil, not an error")
	}
}

// One session per project: saving twice must replace, never accumulate.
func TestRepoSessionUpserts(t *testing.T) {
	ctx := context.Background()
	repo := NewRepo(NewMemoryStore())

	for _, appType := range []string{"pos", "saas"} {
		if err := repo.SaveSession(ctx, Session{ProjectID: "p1", AppType: appType, Current: 3}); err != nil {
			t.Fatal(err)
		}
	}
	got, err := repo.GetSession(ctx, "p1")
	if err != nil || got == nil {
		t.Fatalf("GetSession = %v, %v", got, err)
	}
	if got.AppType != "saas" {
		t.Errorf("the second save should have replaced the first: %q", got.AppType)
	}
	all, _ := repo.Store().Find(ctx, CollSessions, Doc{"project_id": "p1"}, "", 0, 0)
	if len(all) != 1 {
		t.Errorf("sessions stored = %d, want 1", len(all))
	}
}

func TestRepoPlanVersioning(t *testing.T) {
	ctx := context.Background()
	repo := NewRepo(NewMemoryStore())

	for v := 1; v <= 3; v++ {
		if _, err := repo.SavePlan(ctx, PlanRecordDoc{
			ProjectID: "p1", Version: v, Plan: Plan{AppName: "v" + string(rune('0'+v))},
		}); err != nil {
			t.Fatal(err)
		}
	}
	latest, err := repo.LatestPlan(ctx, "p1")
	if err != nil || latest == nil {
		t.Fatalf("LatestPlan = %v, %v", latest, err)
	}
	if latest.Version != 3 {
		t.Errorf("latest version = %d, want 3", latest.Version)
	}
	all, _ := repo.ListPlans(ctx, "p1")
	if len(all) != 3 || all[0].Version != 1 {
		t.Errorf("ListPlans should be ascending: %v", versionsOf(all))
	}
}

// The customization flow depends on "latest" meaning most recently written.
func TestRepoLatestVersionIsByTime(t *testing.T) {
	ctx := context.Background()
	repo := NewRepo(NewMemoryStore())

	first, _ := repo.SaveVersion(ctx, Version{ProjectID: "p1", Version: "2.0.0"})
	second, _ := repo.SaveVersion(ctx, Version{ProjectID: "p1", Version: "1.0.0"})

	latest, err := repo.LatestVersion(ctx, "p1")
	if err != nil || latest == nil {
		t.Fatalf("LatestVersion = %v, %v", latest, err)
	}
	if latest.ID != second.ID {
		t.Errorf("latest = %q (%s), want the most recently written %q",
			latest.ID, latest.Version, second.ID)
	}
	_ = first
}

func TestRepoDiagramsReplace(t *testing.T) {
	ctx := context.Background()
	repo := NewRepo(NewMemoryStore())

	if err := repo.SaveDiagrams(ctx, "p1", []Diagram{{Kind: "erd"}, {Kind: "dfd"}}); err != nil {
		t.Fatal(err)
	}
	if err := repo.SaveDiagrams(ctx, "p1", []Diagram{{Kind: "erd"}}); err != nil {
		t.Fatal(err)
	}
	got, _ := repo.ListDiagrams(ctx, "p1")
	if len(got) != 1 || got[0].Kind != "erd" {
		t.Errorf("saving diagrams should replace, not append: %v", got)
	}
	if got[0].ID == "" {
		t.Error("a stored diagram should be given an id")
	}
}

func TestRepoAnswersRoundTrip(t *testing.T) {
	ctx := context.Background()
	repo := NewRepo(NewMemoryStore())

	want := []Answer{
		{QuestionID: "app_type", Value: "pos"},
		{QuestionID: "auth_roles", Value: []any{"admin", "cashier"}},
	}
	if err := repo.SaveAnswers(ctx, "p1", want); err != nil {
		t.Fatal(err)
	}
	if err := repo.SaveAnswers(ctx, "p1", want); err != nil {
		t.Fatal(err)
	}
	got, err := repo.GetAnswers(ctx, "p1")
	if err != nil || len(got) != 2 {
		t.Fatalf("GetAnswers = %v, %v", got, err)
	}
	if got[0].QuestionID != "app_type" || got[0].Value != "pos" {
		t.Errorf("scalar answer lost: %+v", got[0])
	}
	if list, ok := got[1].Value.([]any); !ok || len(list) != 2 {
		t.Errorf("list answer lost: %+v", got[1])
	}
	all, _ := repo.Store().Find(ctx, CollAnswers, Doc{"project_id": "p1"}, "", 0, 0)
	if len(all) != 1 {
		t.Errorf("answers stored = %d, want one document per project", len(all))
	}
}

func keysOf[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func versionsOf(plans []PlanRecordDoc) []int {
	out := make([]int, 0, len(plans))
	for _, p := range plans {
		out = append(out, p.Version)
	}
	return out
}
