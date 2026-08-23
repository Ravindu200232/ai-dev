package srs

import (
	"strings"
	"testing"
)

// shopPlan is an approved plan with accounts, two roles and two records.
func shopPlan() *Plan {
	return &Plan{
		AppName:       "Corner Shop",
		ProductIntent: "A till and stock book for a corner shop, used by the owner and one cashier.",
		Users: []PlanUser{
			{Role: "Cashier", CanDo: []string{"Take a sale", "View products"}},
			{Role: "Admin", CanDo: []string{"Manage products", "Read the takings"}},
		},
		Screens: []Screen{
			{Name: "Storefront", Route: "/", Purpose: "Browse the products", Who: []string{"Visitor"}},
			{Name: "Sale Terminal", Route: "/sale", Purpose: "Take a sale", Who: []string{"Cashier"}},
			{Name: "Admin Products", Route: "/admin/products", Purpose: "Manage products", Who: []string{"Admin"}},
		},
		Records: []PlanRecord{
			{Name: "Product", Keeps: []string{"Name", "Price", "Quantity", "Status"}},
			{Name: "Sale", Keeps: []string{"Product id", "Total", "Notes"}},
		},
		Workflows: []Journey{
			{Name: "Taking a sale", Who: "Cashier", Steps: []string{"Cashier opens the Sale Terminal", "Cashier takes payment"}},
		},
		Features:      []string{"Barcode scanning", "Daily takings report"},
		AccountPolicy: &AccountPolicy{AccountsRequired: true, RegistrationMode: RegistrationAdmin, ProvisioningRole: "Admin"},
		Assumptions:   []string{"Prices are in one currency."},
		OpenQuestions: []OpenQuestion{{Question: "Which card reader?", Required: false}},
		CustomerNotes: "keep it simple",
	}
}

func composed(t *testing.T) *Document {
	t.Helper()
	project := NewProject("a corner shop till", "English")
	pack := BuildPack("pos", project.RawIdea)
	doc := BuildSRSFromPlan(&project, shopPlan(), pack, &Session{}, project.RawIdea)
	if err := doc.Validate(); err != nil {
		t.Fatalf("the composed document must be valid: %v", err)
	}
	return doc
}

func TestComposeTablesFromRecordsOnly(t *testing.T) {
	doc := composed(t)
	var names []string
	for _, table := range doc.DatabaseDesign.Tables {
		names = append(names, table.TableName)
	}
	want := []string{"users", "roles", "products", "sales"}
	if strings.Join(names, ",") != strings.Join(want, ",") {
		t.Fatalf("tables = %v, want %v", names, want)
	}

	products := doc.DatabaseDesign.Tables[2]
	types := map[string]string{}
	for _, f := range products.Fields {
		types[f.Name] = f.Type
	}
	if types["id"] != "uuid" || types["price"] != "decimal" || types["quantity"] != "integer" {
		t.Errorf("field types = %v", types)
	}
	if types["status"] != "enum" {
		t.Errorf("a status column is an enum: %v", types)
	}
	if types["created_at"] != "datetime" {
		t.Errorf("every record is stamped: %v", types)
	}

	// `product_id` on sales names a table, so it becomes a real foreign key.
	var fk *Field
	for i, f := range doc.DatabaseDesign.Tables[3].Fields {
		if f.Name == "product_id" {
			fk = &doc.DatabaseDesign.Tables[3].Fields[i]
		}
	}
	if fk == nil || fk.Type != "foreign_key" || fk.References != "products.id" {
		t.Fatalf("product_id = %+v", fk)
	}
	found := false
	for _, r := range doc.DatabaseDesign.Relationships {
		if r.From == "sales.product_id" && r.To == "products.id" {
			found = true
		}
	}
	if !found {
		t.Errorf("relationships = %+v", doc.DatabaseDesign.Relationships)
	}
}

func TestComposeSplitsPagesByAccess(t *testing.T) {
	doc := composed(t)
	public := map[string]Page{}
	for _, p := range doc.PublicPages {
		public[p.Route] = p
	}
	protected := map[string]Page{}
	for _, p := range doc.ProtectedPages {
		protected[p.Route] = p
	}

	if _, ok := public["/"]; !ok {
		t.Errorf("a visitor screen is public: %v", keysSorted(public))
	}
	if _, ok := public["/login"]; !ok {
		t.Error("an app with accounts needs somewhere to sign in")
	}
	if _, ok := public["/register"]; ok {
		t.Error("admin-created accounts must not get a public sign-up page")
	}
	if _, ok := public["/admin/users"]; ok {
		t.Error("user management is never public")
	}
	users, ok := protected["/admin/users"]
	if !ok || !containsString(users.AllowedRoles, "admin") {
		t.Errorf("admin-created accounts need a management page: %+v", users)
	}
	sale := protected["/sale"]
	if len(sale.AllowedRoles) != 1 || sale.AllowedRoles[0] != "cashier" {
		t.Errorf("a protected page lists only the roles that may open it: %+v", sale)
	}
	if len(sale.Functions) != 1 || sale.Functions[0] != "Take a sale." {
		t.Errorf("the screen's purpose carries over: %+v", sale.Functions)
	}
}

func TestComposeRequirementsTraceToThePlan(t *testing.T) {
	doc := composed(t)
	var text []string
	for _, fr := range doc.FunctionalRequirements {
		text = append(text, fr.Requirement)
	}
	blob := strings.Join(text, "\n")

	for _, want := range []string{
		"sign in with an email address and password",
		"only the screens their role allows",
		"shall not expose public self-registration",
		"provisioning role create user accounts",
		"allow Cashier users to take a sale",
		"shall provide barcode scanning",
		"support the taking a sale workflow",
	} {
		if !strings.Contains(blob, want) {
			t.Errorf("no requirement covers %q", want)
		}
	}
	if strings.Contains(blob, "visitor create") {
		t.Error("admin-created accounts must not promise public sign-up")
	}

	// CRUD is granted from the duties, not handed out by default.
	if !strings.Contains(blob, "create one product record") {
		t.Error("'Manage products' grants create")
	}
	if strings.Contains(blob, "delete one sale record") {
		t.Error("nobody was granted delete on sales")
	}

	for i, fr := range doc.FunctionalRequirements {
		if fr.ID != requirementID("FR", i+1) {
			t.Fatalf("ids must run in order: %q at %d", fr.ID, i)
		}
	}
}

func TestComposeAPIFollowsThePermissions(t *testing.T) {
	doc := composed(t)
	seen := map[string]Endpoint{}
	for _, e := range doc.APIDesign {
		seen[e.Method+" "+e.Path] = e
	}
	if _, ok := seen["POST /api/products"]; !ok {
		t.Errorf("create was granted on products: %v", keysSorted(seen))
	}
	if _, ok := seen["DELETE /api/sales"]; ok {
		t.Error("an ungranted action must not get an endpoint")
	}
	if _, ok := seen["GET /api/users"]; !ok {
		t.Error("admin-created accounts need a users endpoint")
	}
	if _, ok := seen["GET /api/roles"]; ok {
		t.Error("the roles table is the composer's, not an API resource")
	}
	get := seen["GET /api/products"]
	if get.AuthRequired == nil || *get.AuthRequired {
		t.Errorf("the storefront reads products publicly: %+v", get)
	}
	post := seen["POST /api/products"]
	if post.AuthRequired == nil || !*post.AuthRequired || !containsString(post.AllowedRoles, "admin") {
		t.Errorf("writing products is admin-only: %+v", post)
	}
}

func TestComposeWithoutAccounts(t *testing.T) {
	plan := &Plan{
		AppName:       "Plumb Co",
		ProductIntent: "A one page site for a plumber.",
		Users:         []PlanUser{{Role: "Visitor", CanDo: []string{"Read about the service"}}},
		Screens:       []Screen{{Name: "Home", Route: "/", Purpose: "The landing page", Who: []string{"Visitor"}}},
		Records:       []PlanRecord{{Name: "Enquiry", Keeps: []string{"Name", "Message"}}},
		Features:      []string{"Contact form"},
	}
	project := NewProject("a one page site for a plumber", "English")
	doc := BuildSRSFromPlan(&project, plan, BuildPack("landing", project.RawIdea), &Session{}, "")
	if err := doc.Validate(); err != nil {
		t.Fatalf("validate: %v", err)
	}

	if doc.Auth.LoginRequired {
		t.Error("a visitor-only plan has no login")
	}
	if len(doc.RoleAccessMatrix) != 0 {
		t.Errorf("no accounts means no access matrix: %+v", doc.RoleAccessMatrix)
	}
	for _, table := range doc.DatabaseDesign.Tables {
		if authTableNames[table.TableName] {
			t.Errorf("an app with no login has no %q table", table.TableName)
		}
	}
	for _, page := range doc.PublicPages {
		if page.Route == "/login" {
			t.Error("nothing to sign in to")
		}
	}
	blob := strings.ToLower(strings.Join(doc.SecurityRequirements, " "))
	if strings.Contains(blob, "password") {
		t.Errorf("security requirements mention accounts that do not exist: %v", doc.SecurityRequirements)
	}
	// A landing page gets no CRUD requirements it cannot honour.
	for _, fr := range doc.FunctionalRequirements {
		if strings.Contains(fr.Requirement, "delete one enquiry record") {
			t.Error("a landing page is not a CRUD app")
		}
	}
}

func TestNormalizeAccountPolicyClosesUnsafeSignup(t *testing.T) {
	roles := []Role{{RoleKey: "admin", RoleName: "Admin"}, {RoleKey: "customer", RoleName: "Customer"}}
	open := normalizeAccountPolicy(&AccountPolicy{
		AccountsRequired: true, RegistrationMode: RegistrationOpen, RegistrationRole: "Admin",
	}, roles)
	if open.RegistrationRole != "Customer" || open.SignUpRoute != "/register" {
		t.Errorf("policy = %+v", open)
	}

	closed := normalizeAccountPolicy(&AccountPolicy{
		AccountsRequired: true, RegistrationMode: RegistrationOpen,
	}, []Role{{RoleKey: "admin", RoleName: "Admin"}})
	if closed.RegistrationMode != RegistrationAdmin || closed.RegistrationRole != "" {
		t.Errorf("with only privileged roles, sign-up closes: %+v", closed)
	}
	if closed.ProvisioningRole != "Admin" || closed.AccountManagementRoute != "/admin/users" {
		t.Errorf("policy = %+v", closed)
	}
	if none := normalizeAccountPolicy(nil, roles); none.AccountsRequired || none.RegistrationMode != RegistrationNone {
		t.Errorf("no policy = %+v", none)
	}
}

func TestNaming(t *testing.T) {
	cases := map[string]string{
		"Sale Item": "sale_items", "Category": "categories", "Bus": "buses",
		"day": "days", "Match": "matches", "saleItem": "sale_items",
	}
	for in, want := range cases {
		if got := tableName(in); got != want {
			t.Errorf("tableName(%q) = %q, want %q", in, got, want)
		}
	}
	if got := snakeName("Store Manager!"); got != "store_manager" {
		t.Errorf("snakeName = %q", got)
	}
	if got := snakeName("  "); got != "" {
		t.Errorf("an empty name is no name, not a fallback: %q", got)
	}
	if got := sentence(" Take a sale. "); got != "Take a sale." {
		t.Errorf("sentence = %q", got)
	}
	if got := normalizeName("Sale Items"); got != normalizeName("sale_item") {
		t.Errorf("loose comparison failed: %q", got)
	}
}

func TestEnrichmentScopeGuard(t *testing.T) {
	plan := shopPlan()
	stray := &enrichment{Tables: []Table{{TableName: "suppliers", Fields: []Field{{Name: "id"}}}}}
	err := checkEnrichment(stray, plan, true)
	if err == nil || !strings.Contains(err.Error(), "suppliers") {
		t.Fatalf("a table outside the plan must be refused: %v", err)
	}
	if !strings.Contains(err.Error(), "product") {
		t.Errorf("the refusal should name the plan's records: %v", err)
	}

	inside := &enrichment{
		Tables:                 []Table{{TableName: "products"}, {TableName: "sale"}},
		FunctionalRequirements: []Requirement{{Requirement: "The system shall print a receipt"}},
	}
	if err := checkEnrichment(inside, plan, true); err != nil {
		t.Errorf("a plan-shaped pack should pass: %v", err)
	}
	if err := checkEnrichment(&enrichment{
		Tables: []Table{{TableName: "  "}},
	}, plan, true); err == nil {
		t.Error("a nameless table must be refused")
	}

	noAuth := &enrichment{FunctionalRequirements: []Requirement{
		{Requirement: "The system shall let an admin sign in with a password"},
	}}
	err = checkEnrichment(noAuth, &Plan{}, false)
	if err == nil || !strings.Contains(err.Error(), "no login") {
		t.Fatalf("an app with no accounts must not gain a sign-in requirement: %v", err)
	}
}

func TestMergeEnrichmentStaysInsideThePlan(t *testing.T) {
	doc := composed(t)
	before := len(doc.DatabaseDesign.Tables)
	frCount := len(doc.FunctionalRequirements)

	nullable := true
	mergeEnrichment(doc, &enrichment{
		Tables: []Table{
			{TableName: "products", Description: "Everything the shop sells.",
				Fields: []Field{{Name: "barcode", Type: "string", Nullable: &nullable}, {Name: "name"}}},
			{TableName: "suppliers", Fields: []Field{{Name: "id"}}},
		},
		FunctionalRequirements: []Requirement{
			{Module: "Till", Requirement: "The system shall print a receipt"},
			{Requirement: "The system shall print a receipt"},
		},
		NonFunctionalRequirements: []NonFunctional{{Category: "Security", Requirement: "Receipts must not show the full card number"}},
		RiskPriority:              []Risk{{Area: "Payments", Risk: "Card reader offline"}},
		BusinessWorkflows:         []enrichWorkflow{{Name: "Refunding", Steps: []string{"Find the sale", "Refund it"}}},
	})

	if len(doc.DatabaseDesign.Tables) != before {
		t.Errorf("the merge added a table outside the plan: %d → %d", before, len(doc.DatabaseDesign.Tables))
	}
	products := doc.DatabaseDesign.Tables[2]
	if products.Description != "Everything the shop sells." {
		t.Errorf("description = %q", products.Description)
	}
	if !hasField(products.Fields, "barcode") {
		t.Error("a new column on an approved record is enrichment, not scope")
	}
	if len(doc.FunctionalRequirements) != frCount+1 {
		t.Errorf("a repeated requirement must be dropped: %d", len(doc.FunctionalRequirements))
	}
	last := doc.FunctionalRequirements[len(doc.FunctionalRequirements)-1]
	if last.ID != requirementID("FR", len(doc.FunctionalRequirements)) || last.Requirement != "The system shall print a receipt." {
		t.Errorf("merged requirement = %+v", last)
	}
	if len(doc.RiskPriority) != 0 {
		t.Error("a half-filled risk reads as analysis nobody did")
	}
	if len(doc.BusinessWorkflows) != 1 || doc.BusinessWorkflows[0].WorkflowName != "Refunding" {
		t.Errorf("workflows = %+v", doc.BusinessWorkflows)
	}
	if len(doc.Traceability) != len(doc.FunctionalRequirements) {
		t.Error("the matrix has to be rebuilt after the requirements change")
	}
}

func TestInternationalProfile(t *testing.T) {
	doc := composed(t)
	ApplyInternationalProfile(doc)

	if doc.StandardsProfile["requirements_standard"] != "ISO/IEC/IEEE 29148:2018" {
		t.Errorf("standards profile = %v", doc.StandardsProfile)
	}
	if doc.DocumentControl["document_id"] != "SRS-CORNER-SHOP" {
		t.Errorf("document_id = %v", doc.DocumentControl["document_id"])
	}
	if len(doc.RevisionHistory) != 1 || doc.ApprovalRecord == nil {
		t.Errorf("control = %+v %+v", doc.RevisionHistory, doc.ApprovalRecord)
	}

	for _, fr := range doc.FunctionalRequirements {
		if fr.VerificationMethod == "" || fr.Source == "" || fr.QualityReview == nil {
			t.Fatalf("every requirement is verifiable and sourced: %+v", fr)
		}
	}
	var barcode Requirement
	for _, fr := range doc.FunctionalRequirements {
		if strings.Contains(fr.Requirement, "barcode") {
			barcode = fr
		}
	}
	if barcode.Source != "Approved Plan Feature 1" {
		t.Errorf("a requirement restating a feature says so: %q", barcode.Source)
	}
	for _, nfr := range doc.NonFunctionalRequirements {
		if nfr.Category == "Security" && nfr.VerificationMethod != "Test / Inspection" {
			t.Errorf("security is inspected: %+v", nfr)
		}
		if nfr.Category == "Performance" && nfr.VerificationMethod != "Test / Measurement" {
			t.Errorf("performance is measured: %+v", nfr)
		}
	}
	for _, row := range doc.Traceability {
		if row.TestCase == "" || row.VerificationMethod == "" || row.Source == "" {
			t.Errorf("trace row = %+v", row)
		}
	}
	if doc.RequirementsQualityRev["profile"] != "ISO/IEC/IEEE 29148:2018" {
		t.Errorf("quality review = %v", doc.RequirementsQualityRev)
	}
}

func TestQualityWarnings(t *testing.T) {
	if flags := qualityWarnings("The system shall respond within 2 seconds."); len(flags) != 0 {
		t.Errorf("a testable requirement is clean: %v", flags)
	}
	if flags := qualityWarnings("The app should be user-friendly."); !containsString(flags, "potentially ambiguous wording") {
		t.Errorf("flags = %v", flags)
	}
	if flags := qualityWarnings("It lists and edits and deletes and prints things"); !containsString(flags, "possibly compound requirement") {
		t.Errorf("flags = %v", flags)
	}
	if flags := qualityWarnings(""); !containsString(flags, "empty") {
		t.Errorf("flags = %v", flags)
	}
}

func TestEffectivePlanReadsTheDocumentBack(t *testing.T) {
	doc := composed(t)
	plan := EffectivePlan(doc)

	if plan.ProductIntent != shopPlan().ProductIntent {
		t.Errorf("product_intent = %q", plan.ProductIntent)
	}
	var roles []string
	for _, u := range plan.Users {
		roles = append(roles, u.Role)
	}
	if strings.Join(roles, ",") != "Cashier,Admin" {
		t.Errorf("users = %v", roles)
	}
	if len(plan.Users[0].CanDo) == 0 {
		t.Error("a role's duties come back with it")
	}
	if len(plan.Screens) != len(doc.PublicPages)+len(doc.ProtectedPages) {
		t.Errorf("screens = %d", len(plan.Screens))
	}
	if len(plan.Records) != len(doc.DatabaseDesign.Tables) {
		t.Errorf("records = %d", len(plan.Records))
	}
	for _, r := range plan.Records {
		if containsString(r.Keeps, "id") || containsString(r.Keeps, "created_at") {
			t.Errorf("bookkeeping columns are not things the customer keeps: %+v", r)
		}
	}
	if plan.AccountPolicy == nil || !plan.AccountPolicy.AccountsRequired {
		t.Errorf("account policy = %+v", plan.AccountPolicy)
	}
	if plan.AccountPolicy.RegistrationMode != RegistrationAdmin {
		t.Errorf("registration mode = %q", plan.AccountPolicy.RegistrationMode)
	}

	doc.Auth = Auth{LoginRequired: false}
	if p := EffectivePlan(doc); p.AccountPolicy.AccountsRequired {
		t.Error("a document with no login has no accounts")
	}
}
