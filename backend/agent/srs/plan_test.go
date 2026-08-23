package srs

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"agentforge/agent/core"
)

// hotelSession is an interview far enough along to build a plan from.
func hotelSession() (*Project, *Session) {
	project := NewProject("a hotel booking site where guests reserve rooms online", "English")
	project.DetectedDomain = "Hotel"
	session := &Session{
		ProjectID: project.ID, AppType: "saas",
		Pack:  BuildPack("saas", project.RawIdea),
		Asked: []string{"app_name", "auth_roles", "role_functions:front_desk", "data_tables", "core_outcome", "extra_notes"},
		Answers: map[string]AnswerEntry{
			"app_name":                  {Value: "Blue Bay"},
			"auth_roles":                {Value: []string{"front_desk", "admin"}},
			"role_functions:front_desk": {Value: []string{"Check a guest in", "Take a payment"}},
			"data_tables":               {Value: []string{"bookings"}},
			"table_entities:bookings":   {Value: []string{"guest_name", "check_in"}},
			"core_outcome":              {Value: "a guest completes a booking without calling"},
			"extra_notes":               {Value: "show the spa prices, and a gallery"},
		},
	}
	return &project, session
}

// testService is a service with no model reachable, which is the path a
// machine with no Ollama takes.
func testService(t *testing.T) *Service {
	t.Helper()
	t.Setenv("OLLAMA_HOST", "http://127.0.0.1:1")
	svc := NewService(NewRepo(NewMemoryStore()), core.NewLLM(), core.Paths{})
	svc.Storage = t.TempDir()
	return svc
}

func TestBuildPackMergesDomainAndAppType(t *testing.T) {
	pack := BuildPack("saas", "a hotel booking site with rooms and guests")
	if pack.Domain != "hotel" {
		t.Fatalf("domain = %q, want hotel", pack.Domain)
	}
	if pack.DomainConfidence < DomainFloor {
		t.Fatalf("a plain hotel idea should classify confidently: %v", pack.DomainConfidence)
	}
	if pack.AppLabel == "" || pack.Archetype == "" || pack.QuestionSet == "" {
		t.Errorf("the app type half of the pack is missing: %+v", pack)
	}
	if len(pack.Pages) == 0 || len(pack.DomainTables) == 0 || len(pack.DomainWorkflows) == 0 {
		t.Errorf("pages=%d tables=%d workflows=%d", len(pack.Pages), len(pack.DomainTables), len(pack.DomainWorkflows))
	}
	// A confident domain names the roles, so "guest" and "super_admin" are out.
	for _, role := range pack.Roles {
		if role == "guest" || role == "super_admin" {
			t.Errorf("role %q should not survive into the pack", role)
		}
	}
	if !containsString(pack.Entities, "Booking") {
		t.Errorf("entities should be singular PascalCase domain tables: %v", pack.Entities)
	}
}

func TestBuildPackThinArchetypeKeepsOneRecord(t *testing.T) {
	pack := BuildPack("landing", "a hotel landing page")
	if len(pack.Entities) > 1 {
		t.Errorf("a landing page is not a database: %v", pack.Entities)
	}
	if pack.Archetype != archetypeLanding {
		t.Errorf("archetype = %q", pack.Archetype)
	}
}

func TestBuildOfflinePlanFromAnswers(t *testing.T) {
	project, session := hotelSession()
	plan := BuildOfflinePlan(project, session, "")

	if plan.AppName != "Blue Bay" {
		t.Errorf("app_name = %q", plan.AppName)
	}
	if !strings.Contains(plan.ProductIntent, "a guest completes a booking") {
		t.Errorf("the stated outcome has to reach product_intent: %q", plan.ProductIntent)
	}
	if len(plan.Users) != 2 || plan.Users[0].Role != "Front desk" {
		t.Fatalf("users = %+v", plan.Users)
	}
	if len(plan.Screens) == 0 {
		t.Fatal("a saas pack has pages, so the plan has screens")
	}
	for _, s := range plan.Screens {
		if s.Purpose == "" || len(s.Who) == 0 {
			t.Errorf("every screen needs a purpose and an audience: %+v", s)
		}
	}
	if len(plan.Records) != 1 || plan.Records[0].Name != "Bookings" {
		t.Fatalf("records = %+v", plan.Records)
	}
	if len(plan.Records[0].Keeps) != 2 {
		t.Errorf("keeps = %v", plan.Records[0].Keeps)
	}
	if plan.Workflows[0].Name != "Primary success path" {
		t.Errorf("the stated outcome leads the journeys: %+v", plan.Workflows[0])
	}
	// Everything they typed in the free box has to survive as something.
	if !containsString(plan.Features, "Show the spa prices") || !containsString(plan.Features, "A gallery") {
		t.Errorf("the customer's own words were summarised away: %v", plan.Features)
	}
	if plan.CustomerNotes == "" || plan.LookAndFeel == "" {
		t.Errorf("notes=%q look=%q", plan.CustomerNotes, plan.LookAndFeel)
	}
}

func TestBuildOfflinePlanWithoutRoles(t *testing.T) {
	project := NewProject("a one page site for a plumber", "English")
	session := &Session{AppType: "landing", Pack: BuildPack("landing", project.RawIdea)}
	plan := BuildOfflinePlan(&project, session, "")

	if len(plan.Users) != 1 || plan.Users[0].Role != "Visitor" {
		t.Fatalf("users = %+v", plan.Users)
	}
	if plan.AccountPolicy.AccountsRequired {
		t.Error("a visitor-only site does not need accounts")
	}
	if plan.AccountPolicy.RegistrationMode != RegistrationNone {
		t.Errorf("registration_mode = %q", plan.AccountPolicy.RegistrationMode)
	}
}

func TestAccountPolicyNeverHandsOutPrivilege(t *testing.T) {
	pack := BuildPack("saas", "a booking tool")
	pack.AuthPolicy = "required"
	users := []PlanUser{{Role: "Admin"}, {Role: "Manager"}, {Role: "Customer"}}

	session := &Session{Answers: map[string]AnswerEntry{
		"account_creation": {Value: RegistrationOpen},
		"signup_role":      {Value: "Admin"},
	}}
	policy := accountPolicyFor(session, pack, users)
	if policy.RegistrationRole != "Customer" {
		t.Errorf("public sign-up picked %q; it must fall through to an unprivileged role", policy.RegistrationRole)
	}

	// With nobody unprivileged to fall through to, sign-up closes entirely.
	closed := accountPolicyFor(session, pack, []PlanUser{{Role: "Admin"}})
	if closed.RegistrationMode != RegistrationAdmin || closed.ProvisioningRole != "Admin" {
		t.Errorf("mode=%q provisioning=%q", closed.RegistrationMode, closed.ProvisioningRole)
	}
	if closed.AccountManagementRoute != "/admin/users" {
		t.Errorf("admin-created accounts need somewhere to be created: %q", closed.AccountManagementRoute)
	}

	invite := accountPolicyFor(&Session{Answers: map[string]AnswerEntry{
		"account_creation": {Value: RegistrationInvite},
	}}, pack, users)
	if invite.InvitationAcceptRoute != "/accept-invite" || invite.ProvisioningRole != "Admin" {
		t.Errorf("invite policy = %+v", invite)
	}
}

func TestIdentityContract(t *testing.T) {
	signIn, register := identityContract(nil)
	if strings.Join(signIn, ",") != "email,password" {
		t.Errorf("default sign-in = %v", signIn)
	}
	if !containsString(register, "full_name") {
		t.Errorf("registration should ask for a name: %v", register)
	}

	session := &Session{Answers: map[string]AnswerEntry{
		"auth_identity": {Value: []string{"Mobile Number", "Full Name"}},
	}}
	signIn, register = identityContract(session)
	if strings.Join(signIn, ",") != "phone,password" {
		t.Errorf("a mobile number is a phone: %v", signIn)
	}
	if containsString(signIn, "full_name") {
		t.Error("a name identifies a person but cannot authenticate one")
	}
	if !containsString(register, "phone") || !containsString(register, "full_name") {
		t.Errorf("register = %v", register)
	}
}

func TestSplitNotes(t *testing.T) {
	got := SplitNotes("show the spa prices, and a gallery. Also a contact form")
	want := []string{"Show the spa prices", "A gallery", "Also a contact form"}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Errorf("SplitNotes = %v, want %v", got, want)
	}
	if SplitNotes("   ") != nil {
		t.Error("an empty note is not a feature")
	}
}

func TestMergePlanKeepsWhatTheModelOmitted(t *testing.T) {
	skeleton := &Plan{
		AppName: "Blue Bay", ProductIntent: "the original", LookAndFeel: "light",
		Users:    []PlanUser{{Role: "Admin", CanDo: []string{"Everything"}}},
		Screens:  []Screen{{Name: "Home", Purpose: "landing"}},
		Records:  []PlanRecord{{Name: "Booking", Keeps: []string{"Guest", "Date"}}},
		Features: []string{"Booking"}, CustomerNotes: "their words",
		OpenQuestions: []OpenQuestion{{Question: "which currency?", Required: true}},
	}
	answer := &Plan{
		ProductIntent: "a rewritten intent",
		Features:      []string{"Booking", "Payment"},
		OpenQuestions: []OpenQuestion{},
	}

	merged := mergePlan(skeleton, answer)
	if merged.ProductIntent != "a rewritten intent" {
		t.Errorf("product_intent = %q", merged.ProductIntent)
	}
	if len(merged.Records) != 1 || merged.Records[0].Name != "Booking" {
		t.Errorf("a section the model left out must survive: %+v", merged.Records)
	}
	if len(merged.OpenQuestions) != 0 {
		t.Errorf("an empty question list is a valid answer: %+v", merged.OpenQuestions)
	}
	if merged.CustomerNotes != "their words" {
		t.Error("the customer's own words are never the model's to rewrite")
	}
	if skeleton.ProductIntent != "the original" {
		t.Error("mergePlan must not write through to the plan it was given")
	}
}

func TestCleanQuestionsCapsOptions(t *testing.T) {
	got := cleanQuestions([]OpenQuestion{
		{Question: "  "},
		{Question: "which currency?", Options: []string{"USD", "USD", "EUR", "GBP", "LKR", "JPY"}},
	})
	if len(got) != 1 {
		t.Fatalf("a blank question is not a question: %+v", got)
	}
	if len(got[0].Options) != 4 {
		t.Errorf("options = %v", got[0].Options)
	}
}

func TestJourneysForEveryRole(t *testing.T) {
	plan := &Plan{
		Users: []PlanUser{
			{Role: "Cashier", CanDo: []string{"Take a sale"}},
			{Role: "Admin", CanDo: []string{"Add a product", "Read the takings"}},
		},
		Workflows:     []Journey{{Name: "Taking a sale", Steps: []string{"Cashier opens the till", "Cashier takes payment"}}},
		AccountPolicy: &AccountPolicy{AccountsRequired: true},
	}
	flows := journeysForEveryRole(plan)
	if len(flows) != 2 {
		t.Fatalf("every role needs a journey: %+v", flows)
	}
	if flows[0].Who != "Cashier" {
		t.Errorf("an unattributed journey should find its role in its own steps: %+v", flows[0])
	}
	if flows[1].Who != "Admin" || flows[1].Steps[0] != "Admin signs in" {
		t.Errorf("a built journey starts at the sign-in it needs: %+v", flows[1])
	}

	plan.AccountPolicy = nil
	if got := journeysForEveryRole(plan); got[1].Steps[0] == "Admin signs in" {
		t.Error("without accounts nobody signs in")
	}
	if got := journeysForEveryRole(&Plan{Workflows: []Journey{{Name: "empty"}}}); len(got) != 0 {
		t.Errorf("a journey with no steps is not a journey: %+v", got)
	}
}

func TestCheckPlanDepthFloor(t *testing.T) {
	good := &Plan{
		ProductIntent: strings.Repeat("a hotel booking system for guests and the front desk. ", 4),
		Screens:       []Screen{{Name: "Home", Purpose: "the landing page"}},
		Records:       []PlanRecord{{Name: "Booking", Keeps: []string{"Guest", "Date"}}},
		Workflows:     []Journey{{Name: "Booking", Steps: []string{"search", "pay"}}},
		Features:      []string{"Online booking"},
		Users:         []PlanUser{{Role: "Admin", CanDo: []string{"Add a room", "Refund"}}},
		AccountPolicy: &AccountPolicy{AccountsRequired: true, RegistrationMode: RegistrationOpen, RegistrationRole: "Guest"},
	}
	crud := &Pack{Archetype: archetypeCRUD, AuthPolicy: "required"}
	if err := checkPlan(good, crud); err != nil {
		t.Fatalf("a complete plan should pass: %v", err)
	}

	cases := []struct {
		name string
		edit func(*Plan)
		want string
	}{
		{"thin intent", func(p *Plan) { p.ProductIntent = "a hotel app" }, "product_intent"},
		{"purposeless screen", func(p *Plan) { p.Screens = []Screen{{Name: "Home"}} }, "purpose"},
		{"no features", func(p *Plan) { p.Features = nil }, "capabilities"},
		{"no records", func(p *Plan) { p.Records = nil }, "business record"},
		{"empty record", func(p *Plan) { p.Records = []PlanRecord{{Name: "Booking"}} }, "at least 2"},
		{"shallow record", func(p *Plan) { p.Records = []PlanRecord{{Name: "Booking", Keeps: []string{"Guest"}}} }, "only one thing"},
		{"no journey", func(p *Plan) { p.Workflows = nil }, "workflow"},
		{"thin roles", func(p *Plan) { p.Users = []PlanUser{{Role: "Admin", CanDo: []string{"Everything"}}} }, "can_do"},
		{"no policy", func(p *Plan) { p.AccountPolicy = nil }, "account_policy is required"},
		{"unknown mode", func(p *Plan) { p.AccountPolicy.RegistrationMode = "somehow" }, "registration_mode"},
		{"nameless signup", func(p *Plan) { p.AccountPolicy.RegistrationRole = "" }, "explicit registration_role"},
		{"privileged signup", func(p *Plan) { p.AccountPolicy.RegistrationRole = "Store Manager" }, "privileged role"},
		{"unprovisioned", func(p *Plan) {
			p.AccountPolicy.RegistrationMode = RegistrationAdmin
			p.AccountPolicy.ProvisioningRole = ""
		}, "provisioning_role"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			plan := *good
			policy := *good.AccountPolicy
			plan.AccountPolicy = &policy
			tc.edit(&plan)
			err := checkPlan(&plan, crud)
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Errorf("checkPlan = %v, want it to name %q", err, tc.want)
			}
		})
	}

	// A landing page has no records and no journeys, and that is not a fault.
	thin := &Plan{
		ProductIntent: strings.Repeat("a one page site for a plumber in Colombo. ", 4),
		Screens:       []Screen{{Name: "Home", Purpose: "the landing page"}},
		Features:      []string{"Contact form"},
	}
	if err := checkPlan(thin, &Pack{Archetype: archetypeLanding}); err != nil {
		t.Errorf("a landing page should not be held to the record rules: %v", err)
	}
}

func TestContentHashIsStableAndSensitive(t *testing.T) {
	plan := &Plan{AppName: "Blue Bay", Features: []string{"Booking"}}
	first := ContentHash(plan)
	if len(first) != 16 {
		t.Fatalf("content hash = %q", first)
	}
	if ContentHash(&Plan{AppName: "Blue Bay", Features: []string{"Booking"}}) != first {
		t.Error("the same plan must fingerprint the same way twice")
	}
	plan.Features = append(plan.Features, "Payment")
	if ContentHash(plan) == first {
		t.Error("a changed plan must fingerprint differently")
	}
}

func TestRenderPlanMarkdown(t *testing.T) {
	project, session := hotelSession()
	plan := BuildOfflinePlan(project, session, "")
	plan.OpenQuestions = []OpenQuestion{{Question: "which currency?", Required: true}}
	md := RenderPlanMarkdown(plan, plan.AppName)

	for _, want := range []string{
		"# Blue Bay", "## In your own words", "## Who uses it", "## Account access",
		"## Screens", "| Screen | What it is for | Who sees it |",
		"## What it keeps track of", "## User journeys", "## What it does",
		"## Look and feel", "## Still to settle",
		"**(needed before we can write the SRS)**",
	} {
		if !strings.Contains(md, want) {
			t.Errorf("the review page is missing %q", want)
		}
	}
	if !strings.HasSuffix(md, "\n") {
		t.Error("markdown should end with one newline")
	}
}

func TestPlanStateAndApproval(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewRepo(NewMemoryStore()), nil, core.Paths{})
	project, session := hotelSession()
	if err := svc.Repo.CreateProject(ctx, *project); err != nil {
		t.Fatal(err)
	}

	empty, err := svc.PlanState(ctx, project.ID)
	if err != nil {
		t.Fatal(err)
	}
	if empty.CanApprove || empty.Reason != "no plan has been generated yet" {
		t.Errorf("an empty project = %+v", empty)
	}
	if empty.Version != nil || empty.Versions == nil {
		t.Error("versions must serialise as [] and version as null")
	}

	plan := BuildOfflinePlan(project, session, "")
	plan.OpenQuestions = []OpenQuestion{{Question: "which currency?", Required: true}}
	doc, err := svc.Repo.SavePlan(ctx, PlanRecordDoc{
		ProjectID: project.ID, Version: 1, Plan: *plan, ContentHash: ContentHash(plan),
	})
	if err != nil {
		t.Fatal(err)
	}

	blocked, _ := svc.PlanState(ctx, project.ID)
	if blocked.CanApprove || !strings.Contains(blocked.Reason, "which currency?") {
		t.Errorf("a required question blocks approval: %+v", blocked)
	}
	if blocked.Markdown == "" || blocked.Plan == nil || *blocked.Version != 1 {
		t.Errorf("state = %+v", blocked)
	}
	if got, _ := svc.ApprovePlan(ctx, project.ID, nil); got.Approved {
		t.Error("a blocked plan cannot be approved")
	}

	plan.OpenQuestions = nil
	if err := svc.Repo.UpdatePlan(ctx, doc.ID, Doc{"plan": plan}); err != nil {
		t.Fatal(err)
	}
	stale := 0
	if got, _ := svc.ApprovePlan(ctx, project.ID, &stale); got.Approved {
		t.Error("approving an old version must be refused")
	}
	version := 1
	got, err := svc.ApprovePlan(ctx, project.ID, &version)
	if err != nil || !got.Approved {
		t.Fatalf("ApprovePlan = %+v, %v", got, err)
	}
	after, _ := svc.Repo.GetProject(ctx, project.ID)
	if after.Status != StatusPlanApproved {
		t.Errorf("approval should move the project on: %q", after.Status)
	}
	approved, _ := svc.ApprovedPlan(ctx, project.ID)
	if approved == nil || approved.Version != 1 {
		t.Errorf("ApprovedPlan = %+v", approved)
	}
	if again, _ := svc.ApprovePlan(ctx, project.ID, nil); again.Reason != "already approved" {
		t.Errorf("second approval = %+v", again)
	}
}

// The Studio reads these names directly, so they are a contract.
func TestPlanEnvelopeFieldNames(t *testing.T) {
	raw, err := json.Marshal(&PlanEnvelope{})
	if err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{
		"plan", "markdown", "version", "versions", "content_hash", "change_request",
		"approved", "approved_at", "can_approve", "reason", "open_questions",
	} {
		if _, ok := body[key]; !ok {
			t.Errorf("the plan envelope lost %q", key)
		}
	}

	raw, _ = json.Marshal(&Plan{})
	body = map[string]any{}
	_ = json.Unmarshal(raw, &body)
	for _, key := range []string{
		"app_name", "product_intent", "customer_notes", "look_and_feel", "screens",
		"users", "records", "workflows", "features", "account_policy", "assumptions",
		"open_questions",
	} {
		if _, ok := body[key]; !ok {
			t.Errorf("the plan lost %q", key)
		}
	}

	// PlanReview.jsx reads w.name, not w.workflow_name.
	raw, _ = json.Marshal(Journey{Name: "Booking"})
	if !strings.Contains(string(raw), `"name"`) {
		t.Errorf("a plan journey is keyed by name: %s", raw)
	}
}

func TestPlanPromptCarriesTheEvidence(t *testing.T) {
	project, session := hotelSession()
	project.Classification = map[string]any{"app_type": "saas", "reasoning": "it books rooms"}
	project.SuggestedStack = map[string]any{"frontend": "next", "locked": true}
	session.Questions = []Question{{ID: "app_name", Question: "What is it called?", WhyNeeded: "it names the app"}}
	coverage := map[string]any{"critical_missing": []string{"payments_billing"}}

	skeleton := BuildOfflinePlan(project, session, "")
	prompt := planPrompt(project, session, session.Pack, "", nil, "", coverage, skeleton)

	for _, want := range []string{
		"THE CUSTOMER'S OWN WORDS", "APP TYPE:", "BUSINESS DOMAIN: Hospitality SaaS",
		"WHAT WE HAVE ALREADY WORKED OUT", "frontend=next",
		"WHAT APPS IN THIS TRADE USUALLY HAVE", "WHAT THEY ANSWERED",
		"What is it called? → Blue Bay", "NOBODY ASKED ABOUT THESE",
		"payments_billing", "IN THE CUSTOMER'S OWN WORDS", "A PLAN ASSEMBLED FROM THOSE ANSWERS",
	} {
		if !strings.Contains(prompt, want) {
			t.Errorf("the plan prompt is missing %q", want)
		}
	}
	if strings.Contains(prompt, "locked=") {
		t.Error("`locked` is a flag, not a stack choice")
	}

	previous := &Plan{AppName: "Blue Bay", ProductIntent: "as it stands"}
	revised := planPrompt(project, session, session.Pack, "", previous, "add a spa page", coverage, skeleton)
	if !strings.Contains(revised, "THE PLAN AS IT STANDS") || !strings.Contains(revised, "add a spa page") {
		t.Error("a revision edits the plan they approved, not the answers")
	}
	if !strings.Contains(revised, "A revision is not a re-plan") {
		t.Error("the revision rules are missing")
	}
	if strings.Contains(revised, "A PLAN ASSEMBLED FROM THOSE ANSWERS") {
		t.Error("a revision must not also be handed the skeleton")
	}
}

func TestOutputLanguageRule(t *testing.T) {
	if outputLanguageRule("English", "approved plan") != "" {
		t.Error("saying 'answer in English' only wastes context")
	}
	if !strings.Contains(outputLanguageRule("Sinhala", "approved plan"), "Sinhala") {
		t.Error("a non-English plan has to say so")
	}
}

func TestTranslationValidation(t *testing.T) {
	texts := []string{"Open the /admin/users page", "Take a payment"}
	if err := checkTranslations(texts, []translation{{Index: 0, Text: "a"}}); err == nil {
		t.Error("a short reply must be refused")
	}
	if err := checkTranslations(texts, []translation{{Index: 0, Text: "a"}, {Index: 0, Text: "b"}}); err == nil {
		t.Error("a repeated index must be refused")
	}
	dropped := []translation{{Index: 0, Text: "Open the users page"}, {Index: 1, Text: "Take a payment"}}
	if err := checkTranslations(texts, dropped); err == nil {
		t.Error("a translation that drops a route must be refused")
	}
	kept := []translation{{Index: 0, Text: "Open the /admin/users page"}, {Index: 1, Text: "Take a payment"}}
	if err := checkTranslations(texts, kept); err != nil {
		t.Errorf("a faithful translation should pass: %v", err)
	}
}

func TestCollectAndApplyTextSkipMachineKeys(t *testing.T) {
	tree := map[string]any{
		"app_name": "Blue Bay",
		"screens": []any{map[string]any{
			"name": "Front Desk", "route": "/desk", "purpose": "check guests in",
		}},
	}
	texts := collectText(tree, "", map[string]bool{}, nil)
	if containsString(texts, "Blue Bay") || containsString(texts, "/desk") {
		t.Errorf("names and routes are not prose: %v", texts)
	}
	if !containsString(texts, "check guests in") || !containsString(texts, "Front Desk") {
		t.Errorf("prose was missed: %v", texts)
	}

	applied := applyText(tree, "", map[string]string{"check guests in": "atithi lapicha"}).(map[string]any)
	screen := applied["screens"].([]any)[0].(map[string]any)
	if screen["purpose"] != "atithi lapicha" {
		t.Errorf("purpose = %v", screen["purpose"])
	}
	if screen["route"] != "/desk" || applied["app_name"] != "Blue Bay" {
		t.Errorf("a machine value was rewritten: %v", applied)
	}
}
