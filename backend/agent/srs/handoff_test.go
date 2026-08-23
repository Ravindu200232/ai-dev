package srs

import (
	"encoding/json"
	"strings"
	"testing"
)

func builtHandoff(t *testing.T) (*Handoff, *Document) {
	t.Helper()
	doc := composed(t)
	ApplyInternationalProfile(doc)
	h := BuildHandoff(shopPlan(), doc, BuildPack("pos", "a corner shop till"), true)
	return h, doc
}

func TestHandoffShape(t *testing.T) {
	h, _ := builtHandoff(t)
	if h.HandoffVersion != 5 || h.TargetBuilder != "AgentForge" {
		t.Fatalf("version=%d builder=%q", h.HandoffVersion, h.TargetBuilder)
	}
	if h.AppName != "Corner Shop" {
		t.Errorf("app_name = %q", h.AppName)
	}
	if !h.Auth || !h.AuthContract.Enabled {
		t.Error("this plan has accounts")
	}
	if h.Prompt == "" || len(h.Invariants) != 10 {
		t.Errorf("prompt=%d invariants=%d", len(h.Prompt), len(h.Invariants))
	}

	// The Studio and builder/ read these keys by name.
	raw, err := json.Marshal(h)
	if err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{
		"handoff_version", "target_builder", "appType", "app_name", "product_intent",
		"requirements", "source_requirements", "capability_seeds", "feature_contracts",
		"testing_contract", "models", "relationships", "pages", "apis", "roles",
		"access", "workflows", "traceability", "acceptance_criteria", "validation_rules",
		"notification_rules", "integration_requirements", "reporting_requirements",
		"non_functional_requirements", "constraints", "assumptions", "auth",
		"auth_contract", "invariants", "prompt",
	} {
		if _, ok := body[key]; !ok {
			t.Errorf("the handoff lost %q", key)
		}
	}
}

func TestHandoffRequirementsCarryTheirIDs(t *testing.T) {
	h, doc := builtHandoff(t)
	if len(h.SourceRequirements) != len(doc.FunctionalRequirements) {
		t.Fatalf("source requirements = %d, FRs = %d",
			len(h.SourceRequirements), len(doc.FunctionalRequirements))
	}
	for i, r := range h.SourceRequirements {
		if r.SourceID != doc.FunctionalRequirements[i].ID {
			t.Errorf("source_id = %q, want %q", r.SourceID, doc.FunctionalRequirements[i].ID)
		}
		if r.Proof == nil {
			t.Errorf("%s lost its trace row", r.SourceID)
		}
	}
	for i, r := range h.Requirements {
		if r.ID != "R"+itoa(i+1) {
			t.Fatalf("ids must run in order: %q at %d", r.ID, i)
		}
		if !requirementKinds[r.Kind] {
			t.Errorf("%s has kind %q, which the builder cannot schedule", r.ID, r.Kind)
		}
	}

	// Plan duties and features survive alongside the traced requirements.
	var supplementary []string
	for _, r := range h.Requirements {
		if r.SourceID == "" {
			supplementary = append(supplementary, r.Text)
		}
	}
	blob := strings.Join(supplementary, "\n")
	for _, want := range []string{"Admins can manage products", "Barcode scanning",
		"Signed-out visitors cannot open protected pages"} {
		if !strings.Contains(blob, want) {
			t.Errorf("the handoff dropped %q", want)
		}
	}
}

func TestHandoffPagesAndFiles(t *testing.T) {
	h, _ := builtHandoff(t)
	byRoute := map[string]HandoffPage{}
	for _, p := range h.Pages {
		byRoute[p.Route] = p
	}
	if byRoute["/"].ExpectedFile != "app/page.jsx" {
		t.Errorf("root file = %q", byRoute["/"].ExpectedFile)
	}
	if byRoute["/admin/products"].ExpectedFile != "app/admin/products/page.jsx" {
		t.Errorf("nested file = %q", byRoute["/admin/products"].ExpectedFile)
	}
	if !byRoute["/"].Public || byRoute["/sale"].Public {
		t.Errorf("public flags = %+v %+v", byRoute["/"], byRoute["/sale"])
	}
	if !byRoute["/sale"].LoginRequired {
		t.Error("a protected page requires login")
	}

	for _, ep := range h.APIs {
		if ep.ExpectedFile == "" || !strings.HasSuffix(ep.ExpectedFile, "/route.js") {
			t.Errorf("api file = %q", ep.ExpectedFile)
		}
		if strings.Contains(ep.Path, "{") {
			t.Errorf("a route pattern uses [id], not {id}: %q", ep.Path)
		}
	}
	var byID HandoffAPI
	for _, ep := range h.APIs {
		if ep.Path == "/api/products/[id]" && ep.Method == "PUT" {
			byID = ep
		}
	}
	if byID.ExpectedFile != "app/api/products/[id]/route.js" {
		t.Errorf("dynamic api file = %q", byID.ExpectedFile)
	}
}

func TestHandoffModels(t *testing.T) {
	h, _ := builtHandoff(t)
	byName := map[string]HandoffModel{}
	for _, m := range h.Models {
		byName[m.Name] = m
	}
	if _, ok := byName["Product"]; !ok {
		t.Fatalf("models = %v", keysSorted(byName))
	}
	if _, ok := byName["Sale"]; !ok {
		t.Errorf("`sales` should become `Sale`: %v", keysSorted(byName))
	}

	product := byName["Product"]
	if product.Table != "products" || product.Fields["price"] != "Number" {
		t.Errorf("product = %+v", product)
	}
	sale := byName["Sale"]
	var ref FieldSpec
	for _, f := range sale.FieldSpecs {
		if f.Name == "product_id" {
			ref = f
		}
	}
	if ref.Type != "ObjectId" || ref.References != "products.id" {
		t.Errorf("a relation must survive as ObjectId: %+v", ref)
	}
	for _, f := range product.FieldSpecs {
		if f.Name == "id" && !f.PrimaryKey {
			t.Error("the primary key flag is lost")
		}
		if f.Name == "status" && len(f.Enum) == 0 {
			t.Errorf("enum values are lost: %+v", f)
		}
	}
}

func TestHandoffWorkflowRoutes(t *testing.T) {
	h, _ := builtHandoff(t)
	var sale HandoffWorkflow
	for _, w := range h.Workflows {
		if w.Name == "Taking a sale" {
			sale = w
		}
	}
	if sale.Who != "Cashier" || len(sale.Steps) != 2 {
		t.Fatalf("workflow = %+v", sale)
	}
	if !containsString(sale.Routes, "/sale") {
		t.Errorf("a step naming the terminal should route to it: %+v", sale.Routes)
	}
	if sale.Source != "approved_plan" {
		t.Errorf("source = %q", sale.Source)
	}
	// Every role gets a journey, and the composer's own workflows do not
	// duplicate the plan's.
	names := map[string]int{}
	for _, w := range h.Workflows {
		names[strings.ToLower(w.Name)]++
	}
	for name, n := range names {
		if n > 1 {
			t.Errorf("%q appears %d times", name, n)
		}
	}
}

func TestAuthContractNeverLeaksPrivilege(t *testing.T) {
	h, _ := builtHandoff(t)
	c := h.AuthContract
	if c.SelfRegistration {
		t.Error("this plan is admin-created; there is no public sign-up")
	}
	if c.SignUpRoute != "" {
		t.Errorf("sign_up_route = %q with no self registration", c.SignUpRoute)
	}
	if c.Provisioning == nil || c.Provisioning.Mode != RegistrationAdmin {
		t.Fatalf("provisioning = %+v", c.Provisioning)
	}
	if c.SignInRoute != "/login" || len(c.IdentityFields) == 0 {
		t.Errorf("contract = %+v", c)
	}
	if c.RoleSource == "" || c.WrongRoleAccess != "forbidden" {
		t.Errorf("contract = %+v", c)
	}

	byRole := map[string]RoleRule{}
	for _, row := range c.Roles {
		byRole[row.Role] = row
	}
	cashier, ok := byRole["cashier"]
	if !ok {
		t.Fatalf("roles = %v", keysSorted(byRole))
	}
	if !containsString(cashier.AllowedRoutes, "/sale") {
		t.Errorf("cashier routes = %v", cashier.AllowedRoutes)
	}
	if !containsString(cashier.DeniedRoutes, "/admin/products") {
		t.Errorf("a cashier must be denied the admin page: %v", cashier.DeniedRoutes)
	}
	if cashier.HomeRoute != "/sale" {
		t.Errorf("home = %q", cashier.HomeRoute)
	}
}

func TestAuthContractOpenSignupRejectsPrivilegedRole(t *testing.T) {
	plan := shopPlan()
	plan.AccountPolicy = &AccountPolicy{
		AccountsRequired: true, RegistrationMode: RegistrationOpen, RegistrationRole: "Admin",
	}
	doc := &Document{
		Auth: Auth{
			LoginRequired: true, SelfRegistration: true, RegistrationMode: RegistrationOpen,
			RegistrationRole: "Admin", RegistrationRoles: []string{"Admin"},
			SignInRoute: "/login", SignUpRoute: "/register",
			IdentityFields: []string{"email", "password"},
		},
	}
	c := buildAuthContract(doc, plan, nil,
		[]HandoffRole{{Key: "admin", Name: "Admin"}, {Key: "customer", Name: "Customer"}}, nil, true)

	if containsString(c.RegistrationRoles, "Admin") {
		t.Errorf("public sign-up must never offer a privileged role: %v", c.RegistrationRoles)
	}
	if len(c.RegistrationRoles) != 1 || c.RegistrationRoles[0] != "customer" {
		t.Errorf("registration roles = %v", c.RegistrationRoles)
	}

	// With nothing safe to fall back on, sign-up closes rather than opening.
	closed := buildAuthContract(doc, plan, nil, []HandoffRole{{Key: "admin", Name: "Admin"}}, nil, true)
	if closed.SelfRegistration || closed.SignUpRoute != "" {
		t.Errorf("contract = %+v", closed)
	}
	if closed.RegistrationMode != RegistrationAdmin {
		t.Errorf("mode = %q", closed.RegistrationMode)
	}

	if off := buildAuthContract(doc, plan, nil, nil, nil, false); off.Enabled || off.PublicBehavior == "" {
		t.Errorf("with no accounts = %+v", off)
	}
}

func TestFeatureContractsTargetRealFiles(t *testing.T) {
	h, _ := builtHandoff(t)
	if len(h.FeatureContracts) != len(h.Requirements) {
		t.Fatalf("every requirement gets a capability: %d vs %d",
			len(h.FeatureContracts), len(h.Requirements))
	}
	var traced FeatureContract
	for _, cap := range h.FeatureContracts {
		if len(cap.Routes) > 0 && cap.RequirementID != "" {
			traced = cap
			break
		}
	}
	if traced.ID == "" {
		t.Fatal("no capability landed on a route")
	}
	if !strings.HasPrefix(traced.ID, "CAP-FR-") {
		t.Errorf("capability id = %q", traced.ID)
	}
	if !traced.E2ERequired {
		t.Error("a capability with a route is browser-visible")
	}

	for _, seed := range h.CapabilitySeeds {
		if seed.RequirementID == "" || !strings.HasPrefix(seed.ID, "CAP-") {
			t.Errorf("seed = %+v", seed)
		}
	}
}

func TestTestingContractPreJourney(t *testing.T) {
	h, _ := builtHandoff(t)
	c := h.TestingContract
	if c.ContractVersion != 1 {
		t.Errorf("contract_version = %d", c.ContractVersion)
	}
	if len(c.Unit) != len(h.FeatureContracts) {
		t.Errorf("unit cases = %d, capabilities = %d", len(c.Unit), len(h.FeatureContracts))
	}
	if len(c.E2E) == 0 {
		t.Fatal("no journeys at all")
	}
	if len(c.Coverage.BrowserCapsWithoutE2E) != 0 {
		t.Errorf("every browser capability needs a journey: %v", c.Coverage.BrowserCapsWithoutE2E)
	}

	for _, unit := range c.Unit {
		if len(unit.Assert) == 0 || unit.Act == "" {
			t.Fatalf("unit case = %+v", unit)
		}
		if !containsString(unit.Cases, "success") {
			t.Errorf("cases = %v", unit.Cases)
		}
		if unit.PlannerMustAssign == (len(unit.Targets) > 0) {
			t.Errorf("targets and the planner flag disagree: %+v", unit)
		}
	}

	var protected E2ECase
	for _, e := range c.E2E {
		if e.PreJourney.Account.SignInRequired {
			protected = e
			break
		}
	}
	if protected.ID == "" {
		t.Fatal("no journey needs an account, in an app with accounts")
	}
	account := protected.PreJourney.Account
	if account.Role == "visitor" || !account.NeverReuseAnother {
		t.Errorf("account = %+v", account)
	}
	if protected.PreJourney.InitialRoute != "/login" {
		t.Errorf("a journey that needs signing in starts at sign-in: %q",
			protected.PreJourney.InitialRoute)
	}
	if len(account.IdentityFields) == 0 {
		t.Error("the journey has to know what to type")
	}
	if protected.PreJourney.Database != "fresh_deterministic_seed" {
		t.Errorf("database = %q", protected.PreJourney.Database)
	}
	if len(protected.Proofs) == 0 {
		t.Error("a journey with nothing to prove proves nothing")
	}
}

func TestTestingContractSeedsDynamicRoutes(t *testing.T) {
	auth := AuthContract{Enabled: true, SignInRoute: "/login", IdentityFields: []string{"email"}}
	caps := []FeatureContract{{
		ID: "CAP-FR-001", Requirement: "Read one product", Kind: "list",
		Routes: []string{"/products/[id]"}, Data: []string{"products"}, Roles: []string{"Cashier"},
	}}
	flow := HandoffWorkflow{Name: "Reading a product", Who: "Cashier",
		Steps: []string{"Open the product"}, Routes: []string{"/products/[id]"}}

	pre := preJourney(flow, caps, auth)
	if len(pre.RequiredRecords) != 1 {
		t.Fatalf("a dynamic route needs a seeded record: %+v", pre.RequiredRecords)
	}
	record := pre.RequiredRecords[0]
	if record.Entity != "products" || !record.StableRealID || record.Minimum != 1 {
		t.Errorf("record = %+v", record)
	}
	if !strings.Contains(record.Reason, "dynamic") {
		t.Errorf("reason = %q", record.Reason)
	}

	// A journey with no dynamic route and nothing to list needs no seed.
	plain := preJourney(
		HandoffWorkflow{Name: "Signing in", Routes: []string{"/login"}, Steps: []string{"Sign in"}},
		[]FeatureContract{{ID: "CAP-1", Kind: "feature"}}, auth)
	if len(plain.RequiredRecords) != 0 {
		t.Errorf("records = %+v", plain.RequiredRecords)
	}
}

func TestSynthesizedJourneyForUncoveredCapability(t *testing.T) {
	features := []FeatureContract{
		{ID: "CAP-FR-001", Requirement: "View products", Kind: "list",
			Routes: []string{"/products"}, E2ERequired: true, Acceptance: "The list shows every product"},
	}
	c := buildTestingContract(features, nil, nil, nil, nil, AuthContract{})
	if len(c.E2E) != 1 {
		t.Fatalf("an uncovered browser capability must get a journey: %+v", c.E2E)
	}
	journey := c.E2E[0]
	if !journey.Synthesized || journey.Source != "synthesized_from_uncovered_srs_capability" {
		t.Errorf("journey = %+v", journey)
	}
	if !containsString(journey.Covers, "CAP-FR-001") {
		t.Errorf("covers = %v", journey.Covers)
	}
	if !containsString(journey.Proofs, "The list shows every product") {
		t.Errorf("proofs = %v", journey.Proofs)
	}
	if len(c.Coverage.SynthesizedE2E) != 1 {
		t.Errorf("coverage = %+v", c.Coverage)
	}
}

func TestRoutesForMatchesPagesByName(t *testing.T) {
	pages := []HandoffPage{
		{Name: "Storefront", Route: "/"},
		{Name: "Sale Terminal", Route: "/sale"},
		{Name: "Admin Products", Route: "/admin/products"},
	}
	rolesOf := map[string][]string{"/admin/products": {"admin"}, "/sale": {"cashier"}}

	if got := routesFor("Cashier opens the Sale Terminal", pages, "cashier", rolesOf); len(got) != 1 || got[0] != "/sale" {
		t.Errorf("an exact page name wins: %v", got)
	}
	if got := routesFor("Admin edits products", pages, "admin", rolesOf); !containsString(got, "/admin/products") {
		t.Errorf("routes = %v", got)
	}
	// A cashier cannot be routed through a page only an admin may open.
	if got := routesFor("Cashier edits products", pages, "cashier", rolesOf); containsString(got, "/admin/products") {
		t.Errorf("a journey must not route through a page its role cannot reach: %v", got)
	}
	if got := routesFor("Nothing here matches", pages, "", nil); len(got) != 0 {
		t.Errorf("routes = %v", got)
	}
}

func TestRenderedPromptCarriesTheContract(t *testing.T) {
	h, _ := builtHandoff(t)
	prompt := h.Prompt

	for _, want := range []string{
		"AGENTFORGE BUILD HANDOFF v5", "APP NAME: Corner Shop",
		"AUTHORITATIVE FUNCTIONAL REQUIREMENTS", "FR-001:",
		"PAGES AND EXACT ROUTES", "file=app/sale/page.jsx",
		"USER JOURNEYS — HOW THE PAGES CONNECT",
		"AUTH CONTRACT — IMPLEMENT BEFORE ROLE FEATURES",
		"No public sign-up", "FEATURE LEDGER", "CAP-FR-001",
		"TEST-READY BUILD INPUT", "UNIT TEST CONTRACT", "E2E JOURNEY CONTRACT",
		"PRE-JOURNEY:", "DATA MODEL", "collection/table=products",
		"references products.id", "API CONTRACT", "TRACEABILITY",
		"ACCEPTANCE PROOF", "SECURITY REQUIREMENTS", "DESIGN CONTRACT",
		"FINAL BUILDER CHECK",
	} {
		if !strings.Contains(prompt, want) {
			t.Errorf("the build contract is missing %q", want)
		}
	}
	if strings.Contains(prompt, "\n\n\n") {
		t.Error("blank lines should be collapsed")
	}
	if !strings.HasSuffix(prompt, "\n") {
		t.Error("the prompt should end with one newline")
	}
}

func TestPromptWithoutAccounts(t *testing.T) {
	plan := &Plan{
		AppName:       "Plumb Co",
		ProductIntent: "A one page site for a plumber.",
		Users:         []PlanUser{{Role: "Visitor", CanDo: []string{"Read about the service"}}},
		Screens:       []Screen{{Name: "Home", Route: "/", Purpose: "The landing page", Who: []string{"Visitor"}}},
		Records:       []PlanRecord{{Name: "Enquiry", Keeps: []string{"Name", "Message"}}},
		Features:      []string{"Contact form"},
	}
	project := NewProject("a one page site for a plumber", "English")
	pack := BuildPack("landing", project.RawIdea)
	doc := BuildSRSFromPlan(&project, plan, pack, &Session{}, "")
	h := BuildHandoff(plan, doc, pack, false)

	if h.Auth || h.AuthContract.Enabled {
		t.Error("no accounts")
	}
	if !strings.Contains(h.Prompt, "No accounts. No login, sign-up or protected routes.") {
		t.Error("the prompt must say plainly that there is no login")
	}
	for _, m := range h.Models {
		if m.Name == "User" {
			t.Error("an app with no accounts gets no User model")
		}
	}
	for _, e := range h.TestingContract.E2E {
		if e.PreJourney.Account.SignInRequired {
			t.Errorf("nothing to sign in to: %+v", e.PreJourney.Account)
		}
	}
}

func TestHandoffNaming(t *testing.T) {
	cases := map[string]string{
		"sale_items": "SaleItem", "categories": "Category", "matches": "Match",
		"products": "Product", "": "Record",
	}
	for in, want := range cases {
		if got := entityName(in); got != want {
			t.Errorf("entityName(%q) = %q, want %q", in, got, want)
		}
	}
	if got := routePattern("/api/products/{id}?x=1"); got != "/api/products/[id]" {
		t.Errorf("routePattern = %q", got)
	}
	if got := pageFile("/"); got != "app/page.jsx" {
		t.Errorf("pageFile = %q", got)
	}
	if got := apiFile("/api/products/{id}"); got != "app/api/products/[id]/route.js" {
		t.Errorf("apiFile = %q", got)
	}
	for text, want := range map[string]string{
		"Users can add a new product":  "create",
		"Users can edit the product":   "edit",
		"Users can delete the product": "delete",
		"Users can see a list":         "list",
		"The system reports takings":   "feature",
	} {
		if got := kindFor(text); got != want {
			t.Errorf("kindFor(%q) = %q, want %q", text, got, want)
		}
	}
	if got := schemaType("decimal", ""); got != "Number" {
		t.Errorf("schemaType = %q", got)
	}
	if got := schemaType("string", "products.id"); got != "ObjectId" {
		t.Errorf("a reference is an ObjectId whatever the SRS called it: %q", got)
	}
}
