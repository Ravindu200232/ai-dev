package srs

import (
	"regexp"
	"strings"
)

// The handoff is the contract between the specification and the builder. It is
// the specification restated as things to build: exact routes with the file
// each one lives in, models with real types, endpoints, and every requirement
// carried through with the id it had in the SRS so it can be traced from the
// customer's plan all the way to a test.
//
// Its field names are read by builder/ and by studio/, so they are a contract.

// HandoffVersion is the shape the builder expects. Add a field freely; never
// rename one.
const HandoffVersion = 5

// requirementKinds are the only kinds the builder knows how to schedule.
var requirementKinds = map[string]bool{
	"page": true, "create": true, "edit": true,
	"delete": true, "list": true, "feature": true,
}

// Handoff is the whole contract.
type Handoff struct {
	HandoffVersion int    `json:"handoff_version"`
	TargetBuilder  string `json:"target_builder"`
	AppType        string `json:"appType"`
	AppName        string `json:"app_name"`
	ProductIntent  string `json:"product_intent"`

	Requirements       []HandoffRequirement `json:"requirements"`
	SourceRequirements []HandoffRequirement `json:"source_requirements"`
	CapabilitySeeds    []CapabilitySeed     `json:"capability_seeds"`
	FeatureContracts   []FeatureContract    `json:"feature_contracts"`
	TestingContract    TestingContract      `json:"testing_contract"`

	Models        []HandoffModel        `json:"models"`
	Relationships []HandoffRelationship `json:"relationships"`
	Pages         []HandoffPage         `json:"pages"`
	APIs          []HandoffAPI          `json:"apis"`
	Roles         []HandoffRole         `json:"roles"`
	Access        []RoleAccess          `json:"access"`
	Workflows     []HandoffWorkflow     `json:"workflows"`
	Traceability  map[string]TraceRow   `json:"traceability"`

	AcceptanceCriteria        []Acceptance     `json:"acceptance_criteria"`
	ValidationRules           []ValidationRule `json:"validation_rules"`
	NotificationRules         []Notification   `json:"notification_rules"`
	IntegrationRequirements   []Integration    `json:"integration_requirements"`
	ReportingRequirements     []Reporting      `json:"reporting_requirements"`
	NonFunctionalRequirements []NonFunctional  `json:"non_functional_requirements"`

	Constraints  []string     `json:"constraints"`
	Assumptions  []string     `json:"assumptions"`
	Auth         bool         `json:"auth"`
	AuthContract AuthContract `json:"auth_contract"`
	Invariants   []string     `json:"invariants"`
	Prompt       string       `json:"prompt"`

	PromptLanguage         string `json:"prompt_language,omitempty"`
	SourceDocumentLanguage string `json:"source_document_language,omitempty"`
}

// HandoffRequirement is one thing to build, with where it came from.
type HandoffRequirement struct {
	ID       string    `json:"id"`
	Text     string    `json:"text"`
	Kind     string    `json:"kind"`
	Entity   string    `json:"entity"`
	SourceID string    `json:"source_id"`
	Module   string    `json:"module"`
	Priority string    `json:"priority"`
	Source   string    `json:"source"`
	Proof    *TraceRow `json:"proof"`
	Roles    []string  `json:"roles"`
}

// TraceRow is where one requirement lands: its pages, its data, its test.
type TraceRow struct {
	Module   string   `json:"module"`
	Pages    []string `json:"pages"`
	Tables   []string `json:"tables"`
	TestCase string   `json:"test_case"`
}

type HandoffPage struct {
	Route         string   `json:"route"`
	SRSRoute      string   `json:"srs_route"`
	Name          string   `json:"name"`
	Primary       bool     `json:"primary"`
	Purpose       string   `json:"purpose"`
	Functions     []string `json:"functions"`
	Sections      []string `json:"sections"`
	Public        bool     `json:"public"`
	LoginRequired bool     `json:"login_required"`
	AllowedRoles  []string `json:"allowed_roles"`
	ExpectedFile  string   `json:"expected_file"`
}

type HandoffAPI struct {
	Method       string   `json:"method"`
	Path         string   `json:"path"`
	SRSPath      string   `json:"srs_path"`
	Description  string   `json:"description"`
	AuthRequired *bool    `json:"auth_required"`
	AllowedRoles []string `json:"allowed_roles"`
	ExpectedFile string   `json:"expected_file"`
}

type HandoffModel struct {
	Name        string            `json:"name"`
	Table       string            `json:"table"`
	Description string            `json:"description,omitempty"`
	Fields      map[string]string `json:"fields"`
	FieldSpecs  []FieldSpec       `json:"field_specs"`
	ReadOnly    bool              `json:"readOnly"`
}

// FieldSpec is one column, in the terms the builder writes schemas in.
// `required` is a pointer because "nobody said" is not the same as "optional".
type FieldSpec struct {
	Name       string `json:"name"`
	Type       string `json:"type"`
	SRSType    string `json:"srs_type"`
	References string `json:"references"`
	Required   *bool  `json:"required"`
	Unique     bool   `json:"unique"`
	PrimaryKey bool   `json:"primary_key"`
	Default    any    `json:"default"`
	Enum       []any  `json:"enum"`
}

type HandoffRelationship struct {
	From        string `json:"from"`
	To          string `json:"to"`
	Type        string `json:"type"`
	Via         string `json:"via"`
	Description string `json:"description"`
}

type HandoffRole struct {
	Name        string `json:"name"`
	Key         string `json:"key"`
	Description string `json:"description"`
}

// HandoffWorkflow is one journey with the routes it actually passes through,
// worked out by matching each step against the page names.
type HandoffWorkflow struct {
	Name            string   `json:"name"`
	Who             string   `json:"who"`
	Steps           []string `json:"steps"`
	Routes          []string `json:"routes"`
	Covers          []string `json:"covers"`
	Preconditions   []string `json:"preconditions"`
	ExpectedResults []string `json:"expected_results"`
	Source          string   `json:"source"`
}

// CapabilitySeed is one traced requirement, shaped for the builder's planner.
type CapabilitySeed struct {
	ID            string   `json:"id"`
	RequirementID string   `json:"requirement_id"`
	Requirement   string   `json:"requirement"`
	Module        string   `json:"module"`
	Priority      string   `json:"priority"`
	Roles         []string `json:"roles"`
	Pages         []string `json:"pages"`
	Data          []string `json:"data"`
	TestCase      string   `json:"test_case"`
	E2E           bool     `json:"e2e"`
}

// --- building it -------------------------------------------------------------------

// BuildHandoff turns the specification and the plan into the builder's contract.
func BuildHandoff(plan *Plan, doc *Document, pack *Pack, auth bool) *Handoff {
	if plan == nil {
		plan = &Plan{}
	}
	if pack == nil {
		pack = &Pack{}
	}
	trace := traceMap(doc)

	var requirements []HandoffRequirement
	seen := map[string]bool{}
	add := func(r HandoffRequirement) {
		r.Text = clean(r.Text)
		if r.Text == "" || seen[strings.ToLower(r.Text)] || !requirementKinds[r.Kind] {
			return
		}
		seen[strings.ToLower(r.Text)] = true
		r.ID = "R" + itoa(len(requirements)+1)
		if r.Roles == nil {
			r.Roles = []string{}
		}
		requirements = append(requirements, r)
	}

	// The functional requirements are the product ledger; everything else is
	// context around them.
	for _, fr := range doc.FunctionalRequirements {
		text := clean(fr.Requirement)
		if text == "" {
			continue
		}
		row, traced := trace[fr.ID]
		entity := ""
		if traced && len(row.Tables) > 0 {
			entity = entityName(row.Tables[0])
		}
		var proof *TraceRow
		if traced {
			copied := row
			proof = &copied
		}
		add(HandoffRequirement{
			Text: text, Kind: kindFor(text), Entity: entity, SourceID: fr.ID,
			Module:   clean(firstNonEmpty(fr.Module, row.Module)),
			Priority: clean(firstNonEmpty(fr.Priority, "medium")),
			Source:   "srs.functional_requirements", Proof: proof,
			Roles: cleanList(fr.AllowedRoles),
		})
	}

	pages := handoffPages(plan, doc, auth, add)
	models := handoffModels(plan, doc, pack, auth, add)

	for _, user := range plan.Users {
		role := clean(user.Role)
		if role == "" {
			continue
		}
		for _, duty := range user.CanDo {
			if duty = clean(duty); duty != "" {
				add(HandoffRequirement{
					Text: role + "s can " + lowerFirst(duty), Kind: kindFor(duty),
					Source: "plan.user_duty", Roles: []string{role},
				})
			}
		}
	}
	for _, feature := range plan.Features {
		add(HandoffRequirement{Text: clean(feature), Kind: kindFor(feature), Source: "plan.feature"})
	}
	if auth {
		add(HandoffRequirement{
			Text: "Users can sign in, and signing out immediately redirects to /login",
			Kind: "feature", Entity: "User", Source: "auth.contract",
		})
		for _, p := range pages {
			if signUpPage.MatchString(p.Route + " " + p.Name) {
				add(HandoffRequirement{
					Text: "Visitors can register the approved default user role with email and password",
					Kind: "feature", Entity: "User", Source: "auth.contract",
				})
				break
			}
		}
		add(HandoffRequirement{
			Text: "Signed-out visitors cannot open protected pages",
			Kind: "feature", Source: "auth.contract",
		})
	}

	apis := handoffAPIs(doc)
	roles := handoffRoles(doc)
	workflows := handoffWorkflows(plan, doc, pages)

	var source []HandoffRequirement
	for _, r := range requirements {
		if r.SourceID != "" {
			source = append(source, r)
		}
	}

	authContract := buildAuthContract(doc, plan, pages, roles, doc.RoleAccessMatrix, auth)
	features := buildFeatureContracts(requirements, pages, apis, workflows, doc.AcceptanceCriteria)
	testing := buildTestingContract(features, workflows, pages, apis, doc.ValidationRules, authContract)

	h := &Handoff{
		HandoffVersion: HandoffVersion, TargetBuilder: "AgentForge",
		AppType:       clean(firstNonEmpty(pack.AppLabel, doc.SystemCategory, "Web application")),
		AppName:       clean(firstNonEmpty(doc.AppSummary.AppName, doc.ProjectName, plan.AppName)),
		ProductIntent: clean(firstNonEmpty(plan.ProductIntent, doc.AppSummary.ShortDescription)),

		Requirements: requirements, SourceRequirements: source,
		CapabilitySeeds:  capabilitySeeds(source, pages),
		FeatureContracts: features, TestingContract: testing,

		Models: models, Relationships: handoffRelationships(doc), Pages: pages,
		APIs: apis, Roles: roles, Access: doc.RoleAccessMatrix,
		Workflows: workflows, Traceability: trace,

		AcceptanceCriteria: doc.AcceptanceCriteria, ValidationRules: doc.ValidationRules,
		NotificationRules: doc.NotificationRules, IntegrationRequirements: doc.IntegrationRequirements,
		ReportingRequirements:     doc.ReportingRequirements,
		NonFunctionalRequirements: doc.NonFunctionalRequirements,

		Constraints: doc.Constraints, Assumptions: doc.Assumptions,
		Auth: auth, AuthContract: authContract, Invariants: handoffInvariants,
	}
	h.Prompt = RenderPrompt(h, plan, doc, auth)
	return h
}

// handoffInvariants are the rules that hold whatever the app is. They are
// stated here rather than left implicit because each one is a way a build has
// actually been declared finished while being unusable.
var handoffInvariants = []string{
	"Build the complete approved application; no placeholder pages, TODO features or inert controls.",
	"Every FR/source requirement must become an AgentForge capability with observable proof and exact implementing files.",
	"Every browser-visible capability must be covered by an end-to-end journey; synthesize a journey if the SRS omitted one.",
	"The Builder must implement every unit-test target and every E2E pre-journey fixture, role account, seed record, route and API before QA starts.",
	"Each E2E journey starts independently from deterministic seeded state and uses a distinct account for its exact role.",
	"Every promised route must have a real page/handler and every navigation target must resolve; no orphan or dead routes.",
	"Enforce protected routes and role permissions on the server, not only by hiding UI.",
	"For Mongo ObjectId/reference fields, URL/form/session ids arrive as strings: validate and convert before querying, and serialize ObjectIds to strings at browser boundaries.",
	"A mutation is complete only when it persists the data and the next page/state visibly reflects the result.",
	"Every non-navigation user operation emits an accessible success toast only after confirmed completion and an error toast on failure; toast feedback never replaces durable result proof.",
}

func traceMap(doc *Document) map[string]TraceRow {
	out := map[string]TraceRow{}
	for _, row := range doc.Traceability {
		if rid := clean(row.RequirementID); rid != "" {
			out[rid] = TraceRow{
				Module: clean(row.Module), Pages: cleanList(row.Pages),
				Tables: cleanList(row.Tables), TestCase: clean(row.TestCase),
			}
		}
	}
	return out
}

// handoffPages restates every page as a route and the file that serves it.
func handoffPages(plan *Plan, doc *Document, auth bool, add func(HandoffRequirement)) []HandoffPage {
	srsPages := append(append([]Page{}, doc.PublicPages...), doc.ProtectedPages...)
	publicRoutes := map[string]bool{}
	for _, p := range doc.PublicPages {
		publicRoutes[routePattern(clean(p.Route))] = true
	}

	pages := []HandoffPage{}
	named := 0
	for _, page := range srsPages {
		if clean(page.PageName) != "" {
			named++
		}
	}
	if named > 0 {
		for _, page := range srsPages {
			name := clean(page.PageName)
			if name == "" {
				continue
			}
			raw := clean(page.Route)
			if raw == "" {
				raw = "/" + strings.Trim(slugPattern.ReplaceAllString(strings.ToLower(name), "-"), "-")
			}
			route := routePattern(raw)
			fns := cleanList(page.Functions)
			purpose := strings.Join(fns, "; ")
			isPublic := publicRoutes[route] || (page.LoginRequired != nil && !*page.LoginRequired)
			login := !publicRoutes[route]
			if page.LoginRequired != nil {
				login = *page.LoginRequired
			}
			pages = append(pages, HandoffPage{
				Route: route, SRSRoute: raw, Name: name, Primary: true,
				Purpose: purpose, Functions: fns, Sections: cleanList(page.Sections),
				Public: isPublic, LoginRequired: login,
				AllowedRoles: cleanList(page.AllowedRoles), ExpectedFile: pageFile(route),
			})
			add(HandoffRequirement{
				Text: name + " page at " + route + suffixOf(purpose), Kind: "page", Source: "srs.pages",
			})
		}
		return pages
	}

	// No pages in the document at all, so the plan's screens stand in.
	for i, screen := range plan.Screens {
		name := clean(screen.Name)
		if name == "" {
			continue
		}
		route := routePattern(clean(screen.Route))
		if route == "" || route == "/" {
			if i == 0 {
				route = "/"
			} else {
				route = "/" + strings.Trim(slugPattern.ReplaceAllString(strings.ToLower(name), "-"), "-")
			}
		}
		purpose := clean(screen.Purpose)
		pages = append(pages, HandoffPage{
			Route: route, SRSRoute: route, Name: name, Primary: true,
			Purpose: purpose, Functions: []string{}, Sections: []string{},
			Public: !auth, LoginRequired: auth,
			AllowedRoles: cleanList(screen.Who), ExpectedFile: pageFile(route),
		})
		add(HandoffRequirement{
			Text: name + " page at " + route + suffixOf(purpose), Kind: "page", Source: "plan.screens",
		})
	}
	return pages
}

func suffixOf(purpose string) string {
	if purpose == "" {
		return ""
	}
	return ": " + purpose
}

// handoffModels restates the tables as schemas, and derives CRUD only when the
// specification carried no functional requirements to derive it from.
func handoffModels(plan *Plan, doc *Document, pack *Pack, auth bool, add func(HandoffRequirement)) []HandoffModel {
	models := []HandoffModel{}
	tables := doc.DatabaseDesign.Tables

	haveUsers := false
	for _, t := range tables {
		switch strings.ToLower(t.TableName) {
		case "user", "users":
			haveUsers = true
		}
	}
	if auth && !haveUsers {
		// An app with accounts has a user table whether the plan named one or
		// not; without it there is nothing for a session to point at.
		yes, no := true, false
		specs := []FieldSpec{
			{Name: "name", Type: "String", SRSType: "String", Required: &yes, Enum: []any{}},
			{Name: "email", Type: "String", SRSType: "String", Required: &yes, Unique: true, Enum: []any{}},
			{Name: "role", Type: "String", SRSType: "String", Required: &yes, Enum: []any{}},
		}
		_ = no
		models = append(models, HandoffModel{
			Name: "User", Table: "users", Fields: fieldMap(specs), FieldSpecs: specs,
		})
	}

	archetype := firstNonEmpty(pack.Archetype, archetypeCRUD)
	deriveCRUD := archetype == archetypeCRUD && len(doc.FunctionalRequirements) == 0

	for _, table := range tables {
		name := clean(table.TableName)
		if name == "" {
			continue
		}
		var specs []FieldSpec
		for _, f := range table.Fields {
			if spec := fieldSpec(f); spec.Name != "" {
				specs = append(specs, spec)
			}
		}
		entity := entityName(name)
		models = append(models, HandoffModel{
			Name: entity, Table: name, Description: clean(table.Description),
			Fields: fieldMap(specs), FieldSpecs: specs,
		})

		if !deriveCRUD {
			continue
		}
		label := strings.ReplaceAll(name, "_", " ")
		singular := singularLabel(label)
		add(HandoffRequirement{Text: "Users can add a new " + singular, Kind: "create", Entity: entity, Source: "derived.crud"})
		add(HandoffRequirement{Text: "Users can see a list of " + label, Kind: "list", Entity: entity, Source: "derived.crud"})
		add(HandoffRequirement{Text: "Users can edit an existing " + singular, Kind: "edit", Entity: entity, Source: "derived.crud"})
		add(HandoffRequirement{Text: "Users can delete a " + singular, Kind: "delete", Entity: entity, Source: "derived.crud"})
	}
	return models
}

func fieldMap(specs []FieldSpec) map[string]string {
	out := map[string]string{}
	for _, s := range specs {
		out[s.Name] = s.Type
	}
	return out
}

func fieldSpec(f Field) FieldSpec {
	raw := clean(f.Type)
	if raw == "" {
		raw = "String"
	}
	ref := clean(f.References)
	spec := FieldSpec{
		Name: clean(f.Name), Type: schemaType(raw, ref), SRSType: raw,
		References: ref, Unique: f.Unique, PrimaryKey: f.PrimaryKey,
		Default: f.Default, Enum: f.Values,
	}
	if spec.Enum == nil {
		spec.Enum = []any{}
	}
	if f.Nullable != nil {
		required := !*f.Nullable
		spec.Required = &required
	}
	return spec
}

var typePunctuation = regexp.MustCompile(`[^a-z0-9]+`)

// schemaType translates an SRS data type without losing the relation, which is
// the one thing the builder cannot recover later.
func schemaType(raw, references string) string {
	low := typePunctuation.ReplaceAllString(strings.ToLower(raw), "")
	if references != "" {
		return "ObjectId"
	}
	switch low {
	case "objectid", "mongoid", "reference", "ref", "foreignkey":
		return "ObjectId"
	case "decimal", "integer", "int", "float", "double", "number", "numeric":
		return "Number"
	case "boolean", "bool":
		return "Boolean"
	case "date", "datetime", "timestamp":
		return "Date"
	case "objectidarray", "references":
		return "ObjectId[]"
	case "object", "json", "map", "dict", "dictionary":
		return "Object"
	case "array", "list":
		return "Array"
	}
	if strings.HasSuffix(low, "array") || strings.HasPrefix(low, "arrayof") {
		return "Array"
	}
	if raw == "" {
		return "String"
	}
	return strings.TrimSpace(raw)
}

func handoffAPIs(doc *Document) []HandoffAPI {
	out := []HandoffAPI{}
	for _, ep := range doc.APIDesign {
		raw := clean(ep.Path)
		if raw == "" {
			continue
		}
		path := routePattern(raw)
		out = append(out, HandoffAPI{
			Method: strings.ToUpper(firstNonEmpty(ep.Method, "GET")), Path: path, SRSPath: raw,
			Description: clean(ep.Description), AuthRequired: ep.AuthRequired,
			AllowedRoles: cleanList(ep.AllowedRoles), ExpectedFile: apiFile(path),
		})
	}
	return out
}

func handoffRoles(doc *Document) []HandoffRole {
	out := []HandoffRole{}
	for _, role := range doc.Roles {
		name := clean(firstNonEmpty(role.RoleName, role.RoleKey))
		if name == "" {
			continue
		}
		out = append(out, HandoffRole{
			Name: name, Key: clean(firstNonEmpty(role.RoleKey, strings.ToLower(name))),
			Description: clean(role.Description),
		})
	}
	return out
}

func handoffRelationships(doc *Document) []HandoffRelationship {
	out := []HandoffRelationship{}
	for _, rel := range doc.DatabaseDesign.Relationships {
		out = append(out, HandoffRelationship{
			From: clean(rel.From), To: clean(rel.To), Type: clean(rel.Type),
			Via: clean(rel.Via), Description: clean(rel.Description),
		})
	}
	return out
}

// handoffWorkflows prefers the plan's journeys, which are role-complete, and
// falls back to the document's for anything the plan did not name.
func handoffWorkflows(plan *Plan, doc *Document, pages []HandoffPage) []HandoffWorkflow {
	rolesOf := rolesForRoute(doc)
	out := []HandoffWorkflow{}

	journey := func(name, who string, steps []string, source string) {
		steps = cleanList(steps)
		if len(steps) == 0 {
			return
		}
		var chain []string
		for _, step := range steps {
			for _, route := range routesFor(step, pages, who, rolesOf) {
				if len(chain) == 0 || chain[len(chain)-1] != route {
					chain = append(chain, route)
				}
			}
		}
		out = append(out, HandoffWorkflow{
			Name: clean(firstNonEmpty(name, "Journey")), Who: clean(who), Steps: steps,
			Routes: chain, Covers: []string{}, Preconditions: []string{},
			ExpectedResults: []string{}, Source: source,
		})
	}

	for _, w := range plan.Workflows {
		journey(w.Name, w.Who, w.Steps, "approved_plan")
	}
	for _, w := range doc.BusinessWorkflows {
		name := clean(firstNonEmpty(w.WorkflowName, ""))
		if name == "" {
			continue
		}
		duplicate := false
		for _, existing := range out {
			if strings.EqualFold(existing.Name, name) {
				duplicate = true
				break
			}
		}
		if !duplicate {
			journey(name, w.Who, w.Steps, "srs.business_workflows")
		}
	}
	return out
}

func rolesForRoute(doc *Document) map[string][]string {
	out := map[string][]string{}
	for _, page := range doc.ProtectedPages {
		if route := routePattern(clean(page.Route)); route != "" {
			out[route] = cleanList(page.AllowedRoles)
		}
	}
	for _, page := range doc.PublicPages {
		if route := routePattern(clean(page.Route)); route != "" {
			if _, ok := out[route]; !ok {
				out[route] = []string{}
			}
		}
	}
	return out
}

// capabilitySeeds are the traced requirements, with the roles that can reach
// the pages they land on. The builder's planner starts from these.
func capabilitySeeds(source []HandoffRequirement, pages []HandoffPage) []CapabilitySeed {
	pageRoles := map[string][]string{}
	for _, p := range pages {
		pageRoles[strings.ToLower(p.Name)] = p.AllowedRoles
		pageRoles[strings.ToLower(p.Route)] = p.AllowedRoles
	}

	out := []CapabilitySeed{}
	for _, r := range source {
		proof := TraceRow{}
		if r.Proof != nil {
			proof = *r.Proof
		}
		var roles []string
		for _, page := range proof.Pages {
			for _, role := range pageRoles[strings.ToLower(page)] {
				if !containsString(roles, role) {
					roles = append(roles, role)
				}
			}
		}
		out = append(out, CapabilitySeed{
			ID: "CAP-" + r.SourceID, RequirementID: r.SourceID, Requirement: r.Text,
			Module: r.Module, Priority: r.Priority, Roles: orEmptyList(roles),
			Pages: orEmptyList(proof.Pages), Data: orEmptyList(proof.Tables),
			TestCase: proof.TestCase, E2E: len(proof.Pages) > 0,
		})
	}
	return out
}

// --- naming the builder's files ------------------------------------------------------

var slugPattern = regexp.MustCompile(`[^a-z0-9]+`)
var braceSegment = regexp.MustCompile(`^\{([^}]+)\}$`)
var whitespace = regexp.MustCompile(`\s+`)
var entitySplit = regexp.MustCompile(`[^A-Za-z0-9]+`)

// clean collapses whitespace and drops a trailing full stop, because these
// strings are recombined into sentences and lists.
func clean(text string) string {
	return strings.TrimRight(strings.TrimSpace(whitespace.ReplaceAllString(text, " ")), ".")
}

func cleanList(values []string) []string {
	out := []string{}
	for _, v := range values {
		if v = clean(v); v != "" {
			out = append(out, v)
		}
	}
	return out
}

func orEmptyList(values []string) []string {
	if values == nil {
		return []string{}
	}
	return values
}

// routePattern turns the SRS's `{id}` into the App Router's `[id]`.
func routePattern(route string) string {
	raw := strings.TrimSpace(strings.SplitN(strings.SplitN(route, "?", 2)[0], "#", 2)[0])
	if raw == "" || raw == "/" {
		if raw == "" {
			return ""
		}
		return "/"
	}
	var bits []string
	for _, segment := range strings.Split(strings.Trim(raw, "/"), "/") {
		if segment == "" {
			continue
		}
		if m := braceSegment.FindStringSubmatch(segment); m != nil {
			segment = "[" + m[1] + "]"
		}
		bits = append(bits, segment)
	}
	if len(bits) == 0 {
		return "/"
	}
	return "/" + strings.Join(bits, "/")
}

// pageFile is where the App Router expects this route's page to live.
func pageFile(route string) string {
	path := routePattern(route)
	if path == "" || path == "/" {
		return "app/page.jsx"
	}
	return "app/" + strings.Trim(path, "/") + "/page.jsx"
}

func apiFile(path string) string {
	route := strings.Trim(routePattern(path), "/")
	if route == "" {
		return "app/route.js"
	}
	return "app/" + route + "/route.js"
}

// entityName turns `sale_items` into `SaleItem`, which is what a model is
// called in the generated code. Plural forms ending in a bare `ses` are
// genuinely ambiguous — `houses` is `house` and `buses` is `bus`, and nothing
// in the name says which — so it takes the common case and drops one `s`.
func entityName(name string) string {
	var parts []string
	for _, part := range entitySplit.Split(name, -1) {
		if part == "" {
			continue
		}
		low := strings.ToLower(part)
		switch {
		case strings.HasSuffix(low, "ies"):
			part = part[:len(part)-3] + "y"
		case strings.HasSuffix(low, "ches"), strings.HasSuffix(low, "shes"),
			strings.HasSuffix(low, "sses"), strings.HasSuffix(low, "xes"):
			part = part[:len(part)-2]
		case strings.HasSuffix(low, "s") && !strings.HasSuffix(low, "ss"):
			part = part[:len(part)-1]
		}
		if part != "" {
			parts = append(parts, strings.ToUpper(part[:1])+part[1:])
		}
	}
	if len(parts) == 0 {
		return "Record"
	}
	return strings.Join(parts, "")
}

var createVerb = regexp.MustCompile(`(?i)\b(create|add|register|submit|book|place)\b`)
var editVerb = regexp.MustCompile(`(?i)\b(update|edit|modify|change|mark|approve|assign)\b`)
var deleteVerb = regexp.MustCompile(`(?i)\b(delete|remove|cancel)\b`)
var listVerb = regexp.MustCompile(`(?i)\b(list|browse|search|filter|view|see|show|history)\b`)

// kindFor is what sort of thing this requirement asks to be built, which is
// how the builder decides what a finished version of it looks like.
func kindFor(text string) string {
	switch {
	case createVerb.MatchString(text):
		return "create"
	case editVerb.MatchString(text):
		return "edit"
	case deleteVerb.MatchString(text):
		return "delete"
	case listVerb.MatchString(text):
		return "list"
	}
	return "feature"
}
