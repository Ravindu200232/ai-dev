package srs

import (
	"regexp"
	"sort"
	"strings"
)

// What has to be true before a test can run at all.
//
// This is the point of the whole handoff. QA guessing at fixtures is how a
// journey ends up signing in as the wrong role against an empty database and
// reporting a failure the app does not have. So the setup is written down
// here, as the builder's work, before QA ever starts.

// --- what has to be true before a test runs ----------------------------------------------

// TestingContract is the deterministic input QA runs against.
type TestingContract struct {
	ContractVersion       int              `json:"contract_version"`
	Unit                  []UnitCase       `json:"unit"`
	E2E                   []E2ECase        `json:"e2e"`
	SharedValidationCases []ValidationRule `json:"shared_validation_cases"`
	Coverage              TestCoverage     `json:"coverage"`
	Rules                 []string         `json:"rules"`
}

type TestCoverage struct {
	AllCapabilities         []string `json:"all_capabilities"`
	BrowserCapabilities     []string `json:"browser_capabilities"`
	RequirementsWithoutUnit []string `json:"requirements_without_unit"`
	BrowserCapsWithoutE2E   []string `json:"browser_capabilities_without_e2e"`
	SynthesizedE2E          []string `json:"synthesized_e2e"`
}

type UnitCase struct {
	ID                string   `json:"id"`
	Covers            []string `json:"covers"`
	Requirement       string   `json:"requirement"`
	Targets           []string `json:"targets"`
	PlannerMustAssign bool     `json:"planner_must_assign_target"`
	Arrange           Arrange  `json:"arrange"`
	Act               string   `json:"act"`
	Assert            []string `json:"assert"`
	Cases             []string `json:"cases"`
}

type Arrange struct {
	Session         string           `json:"session"`
	DataFixtures    []string         `json:"data_fixtures"`
	ValidationRules []ValidationRule `json:"validation_rules"`
}

type E2ECase struct {
	ID               string     `json:"id"`
	Name             string     `json:"name"`
	Actor            string     `json:"actor"`
	Source           string     `json:"source"`
	Synthesized      bool       `json:"synthesized"`
	Covers           []string   `json:"covers"`
	Routes           []string   `json:"routes"`
	PreJourney       PreJourney `json:"pre_journey"`
	Steps            []string   `json:"steps"`
	RequiredActions  []string   `json:"required_actions"`
	Proofs           []string   `json:"proofs"`
	SelectorContract string     `json:"selector_contract"`
}

// PreJourney is everything that must already be true for a journey to start.
// It is the builder's work, not QA's guess.
type PreJourney struct {
	Database              string           `json:"database"`
	ExplicitPreconditions []string         `json:"explicit_preconditions"`
	Account               JourneyAccount   `json:"account"`
	SeedEntities          []string         `json:"seed_entities"`
	RequiredRecords       []RequiredRecord `json:"required_records"`
	RequiredRoutes        []string         `json:"required_routes"`
	RequiredAPIs          []string         `json:"required_apis"`
	InitialRoute          string           `json:"initial_route"`
	DependsOnPriorE2E     bool             `json:"depends_on_prior_e2e"`
}

type JourneyAccount struct {
	SignInRequired    bool     `json:"sign_in_required"`
	Role              string   `json:"role"`
	Fixture           string   `json:"fixture"`
	IdentityFields    []string `json:"identity_fields"`
	SignInRoute       string   `json:"sign_in_route"`
	RoleHome          string   `json:"role_home"`
	NeverReuseAnother bool     `json:"never_reuse_another_role"`
}

type RequiredRecord struct {
	Entity       string `json:"entity"`
	Minimum      int    `json:"minimum"`
	StableRealID bool   `json:"stable_real_id"`
	Reason       string `json:"reason"`
}

func buildTestingContract(features []FeatureContract, workflows []HandoffWorkflow,
	pages []HandoffPage, apis []HandoffAPI, validations []ValidationRule,
	auth AuthContract) TestingContract {

	unit := unitCases(features, pages, apis, validations, auth)

	e2e := []E2ECase{}
	covered := map[string]bool{}
	for _, flow := range workflows {
		caps := matchingCaps(flow, features)
		if len(caps) == 0 {
			continue
		}
		e2e = append(e2e, e2eCase(flow, caps, len(e2e)+1, auth, false))
		for _, id := range e2e[len(e2e)-1].Covers {
			covered[id] = true
		}
	}

	// A capability the browser can see and no journey walks is a capability
	// nobody will notice is broken, so one is written for it.
	var browser []FeatureContract
	for _, cap := range features {
		if cap.E2ERequired {
			browser = append(browser, cap)
		}
	}
	synthesized := []string{}
	for _, cap := range browser {
		if covered[cap.ID] {
			continue
		}
		role := ""
		if len(cap.Roles) > 0 {
			role = cap.Roles[0]
		}
		proof := clean(cap.Acceptance)
		steps := []string{}
		if len(cap.Routes) > 0 {
			steps = append(steps, "Open "+cap.Routes[0])
		}
		steps = append(steps, clean(firstNonEmpty(cap.Requirement, cap.ID)))
		if proof != "" {
			steps = append(steps, proof)
		} else {
			steps = append(steps, "Verify the durable visible result for "+cap.ID)
		}
		var expected []string
		if proof != "" {
			expected = []string{proof}
		}
		flow := HandoffWorkflow{
			Name: "Proof for " + cap.ID, Who: role, Covers: []string{cap.ID},
			Routes: cap.Routes, Steps: steps, ExpectedResults: expected,
		}
		e2e = append(e2e, e2eCase(flow, []FeatureContract{cap}, len(e2e)+1, auth, true))
		synthesized = append(synthesized, e2e[len(e2e)-1].ID)
		covered[cap.ID] = true
	}

	unitCovered := map[string]bool{}
	for _, c := range unit {
		for _, id := range c.Covers {
			unitCovered[id] = true
		}
	}
	all, withoutUnit := []string{}, []string{}
	for _, cap := range features {
		all = append(all, cap.ID)
		if !unitCovered[cap.ID] {
			withoutUnit = append(withoutUnit, cap.ID)
		}
	}
	browserIDs, withoutE2E := []string{}, []string{}
	for _, cap := range browser {
		browserIDs = append(browserIDs, cap.ID)
		if !covered[cap.ID] {
			withoutE2E = append(withoutE2E, cap.ID)
		}
	}
	sort.Strings(withoutE2E)

	return TestingContract{
		ContractVersion: 1, Unit: unit, E2E: e2e,
		SharedValidationCases: orEmptyRules(validations),
		Coverage: TestCoverage{
			AllCapabilities: all, BrowserCapabilities: browserIDs,
			RequirementsWithoutUnit: withoutUnit, BrowserCapsWithoutE2E: withoutE2E,
			SynthesizedE2E: synthesized,
		},
		Rules: []string{
			"Builder implements every pre_journey fixture before handing the app to QA.",
			"Each E2E journey starts from a fresh deterministic seed and is independent unless the SRS explicitly declares a dependency.",
			"Use one distinct seeded identity per role; never authenticate a role journey with another role's account.",
			"Dynamic routes use real seeded ids and required parent/reference records.",
			"Unit coverage includes success, validation, dependency failure, signed-out and wrong-role cases whenever applicable.",
			"A passing test must prove durable product behavior; never fake, skip or weaken a test to make the handoff pass.",
		},
	}
}

func orEmptyRules(rules []ValidationRule) []ValidationRule {
	if rules == nil {
		return []ValidationRule{}
	}
	return rules
}

var caseIDPunctuation = regexp.MustCompile(`[^A-Za-z0-9-]+`)

func caseID(prefix, value string, number int) string {
	safe := strings.Trim(caseIDPunctuation.ReplaceAllString(value, "-"), "-")
	if safe == "" {
		return prefix + "-" + requirementID("", number)[1:]
	}
	return prefix + "-" + safe
}

func unitCases(features []FeatureContract, pages []HandoffPage, apis []HandoffAPI,
	validations []ValidationRule, auth AuthContract) []UnitCase {

	out := []UnitCase{}
	for i, cap := range features {
		kinds := []string{"success"}
		switch cap.Kind {
		case "create", "edit", "delete":
			kinds = append(kinds, "invalid_input", "dependency_failure")
		default:
			if len(cap.APIs) > 0 {
				kinds = append(kinds, "invalid_input", "dependency_failure")
			}
		}
		if auth.Enabled && len(cap.Roles) > 0 {
			kinds = append(kinds, "signed_out_rejected", "wrong_role_forbidden")
		}

		assertion := clean(cap.Acceptance)
		if assertion == "" {
			assertion = "Durably and visibly prove: " + clean(firstNonEmpty(cap.Requirement, cap.ID))
		}
		session := "public_or_not_applicable"
		if auth.Enabled && len(cap.Roles) > 0 {
			session = "authenticated_as_one_of: " + strings.Join(cap.Roles, ", ")
		}
		targets := targetFiles(cap, pages, apis)

		out = append(out, UnitCase{
			ID:     caseID("UT", firstNonEmpty(cap.RequirementID, cap.ID), i+1),
			Covers: orEmptyList(nonEmpty(cap.ID)), Requirement: clean(cap.Requirement),
			Targets: targets, PlannerMustAssign: len(targets) == 0,
			Arrange: Arrange{Session: session, DataFixtures: orEmptyList(cap.Data),
				ValidationRules: orEmptyRules(validations)},
			Act:    clean(firstNonEmpty(cap.Requirement, cap.ID)),
			Assert: []string{assertion}, Cases: kinds,
		})
	}
	return out
}

func nonEmpty(value string) []string {
	if value == "" {
		return nil
	}
	return []string{value}
}

// targetFiles is the exact files this capability lives in, so a unit test has
// something concrete to import rather than a name to guess at.
func targetFiles(cap FeatureContract, pages []HandoffPage, apis []HandoffAPI) []string {
	byRoute := map[string]HandoffPage{}
	byName := map[string]HandoffPage{}
	for _, page := range pages {
		byRoute[page.Route] = page
		byName[strings.ToLower(page.Name)] = page
	}
	byKey := map[string]HandoffAPI{}
	for _, ep := range apis {
		byKey[strings.ToUpper(firstNonEmpty(ep.Method, "GET"))+" "+ep.Path] = ep
	}

	files := []string{}
	appendFile := func(file string) {
		if file != "" && !containsString(files, file) {
			files = append(files, file)
		}
	}
	for _, route := range cap.Routes {
		appendFile(byRoute[route].ExpectedFile)
	}
	for _, name := range cap.Pages {
		appendFile(byName[strings.ToLower(name)].ExpectedFile)
	}
	for _, key := range cap.APIs {
		appendFile(byKey[key].ExpectedFile)
	}
	return files
}

func matchingCaps(flow HandoffWorkflow, features []FeatureContract) []FeatureContract {
	explicit := map[string]bool{}
	byRequirement := map[string]string{}
	for _, cap := range features {
		if cap.RequirementID != "" {
			byRequirement[strings.ToLower(cap.RequirementID)] = cap.ID
		}
	}
	for _, value := range flow.Covers {
		id := clean(value)
		if canonical, ok := byRequirement[strings.ToLower(id)]; ok {
			id = canonical
		}
		explicit[id] = true
	}

	name := strings.ToLower(clean(flow.Name))
	var out []FeatureContract
	for _, cap := range features {
		if explicit[cap.ID] {
			out = append(out, cap)
			continue
		}
		matched := false
		if name != "" {
			for _, w := range cap.Workflows {
				if strings.EqualFold(clean(w), name) {
					matched = true
					break
				}
			}
		}
		if !matched {
			for _, route := range flow.Routes {
				if containsString(cap.Routes, route) {
					matched = true
					break
				}
			}
		}
		if matched {
			out = append(out, cap)
		}
	}
	return out
}

func e2eCase(flow HandoffWorkflow, caps []FeatureContract, number int,
	auth AuthContract, synthesized bool) E2ECase {

	proofs := []string{}
	for _, text := range cleanList(flow.ExpectedResults) {
		if !containsString(proofs, text) {
			proofs = append(proofs, text)
		}
	}
	for _, cap := range caps {
		text := clean(cap.Acceptance)
		if text == "" {
			text = "Durably and visibly prove: " + clean(cap.Requirement)
		}
		if !containsString(proofs, text) {
			proofs = append(proofs, text)
		}
	}
	if len(proofs) == 0 {
		proofs = []string{"The final visible state proves every covered capability"}
	}

	var covers, actions []string
	for _, cap := range caps {
		if cap.ID != "" && !containsString(covers, cap.ID) {
			covers = append(covers, cap.ID)
		}
		if cap.Requirement != "" && !containsString(actions, cap.Requirement) {
			actions = append(actions, cap.Requirement)
		}
	}

	source := flow.Source
	if synthesized {
		source = "synthesized_from_uncovered_srs_capability"
	}
	return E2ECase{
		ID:     "E2E-" + requirementID("", number)[1:],
		Name:   clean(firstNonEmpty(flow.Name, "Journey "+itoa(number))),
		Actor:  clean(firstNonEmpty(flow.Who, "visitor")),
		Source: source, Synthesized: synthesized,
		Covers: orEmptyList(covers), Routes: orEmptyList(dedupe(flow.Routes)),
		PreJourney: preJourney(flow, caps, auth),
		Steps:      cleanList(flow.Steps), RequiredActions: orEmptyList(actions),
		Proofs: proofs,
		SelectorContract: "Builder assigns stable accessible names to every action and " +
			"field used here; use literal data-testid only when an accessible selector " +
			"cannot be stable.",
	}
}

func dedupe(values []string) []string {
	out := []string{}
	for _, v := range values {
		if v != "" && !containsString(out, v) {
			out = append(out, v)
		}
	}
	return out
}

// preJourney works out what has to exist before this journey can start: a
// signed-in account of the right role, and a real record behind any dynamic
// route, because `[id]` with nothing to put in it is a 404 dressed as a test
// failure.
func preJourney(flow HandoffWorkflow, caps []FeatureContract, auth AuthContract) PreJourney {
	routes := dedupe(flow.Routes)
	var apis, entities, roles []string
	for _, cap := range caps {
		for _, api := range cap.APIs {
			if !containsString(apis, api) {
				apis = append(apis, api)
			}
		}
		for _, entity := range cap.Data {
			if !containsString(entities, entity) {
				entities = append(entities, entity)
			}
		}
	}
	if flow.Who != "" {
		roles = append(roles, flow.Who)
	}
	for _, cap := range caps {
		for _, role := range cap.Roles {
			if role != "" && !containsString(roles, role) {
				roles = append(roles, role)
			}
		}
	}
	role := ""
	if len(roles) > 0 {
		role = roles[0]
	}

	protected := role != ""
	if !protected {
		// The journey named nobody, so whoever may reach its routes is the
		// actor. Signing in as "visitor" is not a fixture anyone can build.
		for _, rule := range auth.Roles {
			for _, route := range rule.AllowedRoutes {
				if containsString(routes, route) {
					protected, role = true, rule.Role
					break
				}
			}
			if protected {
				break
			}
		}
	}
	signIn := auth.Enabled && protected

	dynamic := false
	for _, route := range routes {
		if strings.Contains(route, "[") {
			dynamic = true
		}
	}
	for _, api := range apis {
		if strings.Contains(api, "[") {
			dynamic = true
		}
	}
	needsRecord := dynamic
	if !needsRecord {
		for _, cap := range caps {
			switch cap.Kind {
			case "list", "edit", "delete":
				needsRecord = true
			}
		}
	}

	records := []RequiredRecord{}
	if needsRecord {
		seeds := entities
		if len(seeds) == 0 {
			seeds = []string{"the journey record"}
		}
		reason := "provide deterministic visible data for the journey"
		if dynamic {
			reason = "resolve a dynamic journey route/API with a real seeded id"
		}
		for _, entity := range seeds {
			records = append(records, RequiredRecord{
				Entity: entity, Minimum: 1, StableRealID: dynamic, Reason: reason,
			})
		}
	}

	account := JourneyAccount{
		SignInRequired: signIn, Role: firstNonEmpty(role, "visitor"),
		Fixture: "no account required", IdentityFields: []string{},
	}
	initial := "/"
	if len(routes) > 0 {
		initial = routes[0]
	}
	if signIn {
		account.Fixture = "one distinct seeded account for this exact role"
		account.IdentityFields = orEmptyList(auth.IdentityFields)
		account.SignInRoute = auth.SignInRoute
		account.NeverReuseAnother = true
		initial = auth.SignInRoute
	}
	if role != "" {
		account.RoleHome = auth.RoleHome[role]
	}

	return PreJourney{
		Database:              "fresh_deterministic_seed",
		ExplicitPreconditions: cleanList(flow.Preconditions),
		Account:               account, SeedEntities: orEmptyList(entities),
		RequiredRecords: records, RequiredRoutes: orEmptyList(routes),
		RequiredAPIs: orEmptyList(apis), InitialRoute: initial,
	}
}

// testingPromptLines writes the test setup out in full. Losing the pre-journey
// detail here is how QA ends up inventing its own fixtures.
func testingPromptLines(contract TestingContract) []string {
	if len(contract.Unit) == 0 && len(contract.E2E) == 0 {
		return nil
	}
	lines := []string{
		"TEST-READY BUILD INPUT — BUILDER OWNS THIS BEFORE QA:",
		"- Build the code, fixtures and stable UI needed by these contracts. Pre-journey setup is implementation work, not something QA should guess.",
		"UNIT TEST CONTRACT:",
	}
	for _, c := range contract.Unit {
		targets := orText(strings.Join(c.Targets, ","), "planner must assign an exact implementation file")
		lines = append(lines,
			"- "+c.ID+" covers="+strings.Join(c.Covers, ",")+" targets="+targets,
			"    arrange: session="+c.Arrange.Session+"; data="+
				orText(strings.Join(c.Arrange.DataFixtures, ","), "none"),
			"    act: "+c.Act,
			"    assert: "+strings.Join(c.Assert, "; "),
			"    cases: "+strings.Join(c.Cases, ", "))
	}

	lines = append(lines, "", "E2E JOURNEY CONTRACT:")
	for _, c := range contract.E2E {
		pre, account := c.PreJourney, c.PreJourney.Account
		lines = append(lines,
			"- "+c.ID+" "+c.Name+" actor="+c.Actor+" covers="+strings.Join(c.Covers, ","),
			"    PRE-JOURNEY: start="+pre.InitialRoute+
				"; seed="+orText(strings.Join(pre.SeedEntities, ","), "fresh empty database")+
				"; role="+account.Role+"; account="+account.Fixture+
				"; routes="+orText(strings.Join(pre.RequiredRoutes, ","), "none")+
				"; apis="+orText(strings.Join(pre.RequiredAPIs, ","), "none"))
		for _, condition := range pre.ExplicitPreconditions {
			lines = append(lines, "    precondition: "+condition)
		}
		for _, record := range pre.RequiredRecords {
			stable := "false"
			if record.StableRealID {
				stable = "true"
			}
			lines = append(lines, "    seeded record: "+record.Entity+
				" minimum="+itoa(record.Minimum)+" stable_real_id="+stable)
		}
		for _, step := range c.Steps {
			lines = append(lines, "    step: "+step)
		}
		for _, action := range c.RequiredActions {
			lines = append(lines, "    required action: "+action)
		}
		for _, proof := range c.Proofs {
			lines = append(lines, "    proof: "+proof)
		}
	}

	lines = append(lines, "", "TEST HANDOFF RULES:")
	for _, rule := range contract.Rules {
		lines = append(lines, "- "+rule)
	}
	return append(lines, "")
}
