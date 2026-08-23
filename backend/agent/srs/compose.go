package srs

import (
	"strings"
)

// The specification is composed from the approved plan and nothing else. The
// plan's records become the tables, its users the roles, its screens the pages
// and its features the acceptance criteria — so everything in the finished
// document traces back to something the customer read and signed.
//
// The Python kept a second composer for projects with no plan at all, built
// straight from the domain template. That path is gone: when there is no plan
// the skeleton one is built first (BuildOfflinePlan) and composed the same way,
// so there is one composition to maintain and one to test.

// BuildSRSFromPlan is a complete, valid specification containing only what the
// plan contains.
func BuildSRSFromPlan(project *Project, plan *Plan, pack *Pack, session *Session, brief string) *Document {
	if project == nil {
		project = &Project{}
	}
	if pack == nil {
		pack = &Pack{}
	}
	auth := AuthOn(pack, plan)
	archetype := firstNonEmpty(pack.Archetype, archetypeCRUD)

	// The plan is the boundary, and a rename is an ordinary plan revision, so
	// the approved name wins over the one the interview first captured.
	appName := firstText(plan.AppName, answerOf(session, "app_name"), project.Title, "Application")
	roles := rolesFromPlan(plan)

	policy := &AccountPolicy{AccountsRequired: false, RegistrationMode: RegistrationNone}
	if auth {
		from := plan.AccountPolicy
		if from == nil {
			from = &AccountPolicy{}
		}
		settled := *from
		settled.AccountsRequired = true
		policy = normalizeAccountPolicy(&settled, roles)
	}

	tables, relationships := tablesFromPlan(plan, auth)
	public, protected := pagesFromPlan(plan, auth, policy)
	frs := requirementsFromPlan(plan, tables, auth, archetype, policy, public)

	devices := asList(answerOf(session, "responsive_pwa"))
	branding := buildBranding(session, pack, appName)
	intent := strings.TrimSpace(plan.ProductIntent)

	doc := &Document{
		DocumentTitle:    "Software Requirements Specification",
		ProjectName:      appName,
		Version:          firstNonEmpty(project.CurrentVersion, "1.0.0"),
		DocumentLanguage: firstNonEmpty(project.Language, "English"),
		SystemCategory:   firstNonEmpty(pack.AppLabel, "Web Application"),
		PreparedFor:      "Approved plan build",

		AppSummary: AppSummary{
			AppName:          appName,
			ShortDescription: firstNonEmpty(intent, truncate(firstNonEmpty(brief, project.RawIdea), 400)),
			BusinessGoal:     firstNonEmpty(intent, "To deliver "+appName+" as described in the approved plan."),
			TargetUsers:      roleNames(roles),
		},
		AppType: AppType{
			Key:          firstNonEmpty(pack.AppType, "other"),
			PrimaryType:  firstNonEmpty(pack.AppLabel, "Web Application"),
			FrontendType: "Responsive web application",
			BackendType:  "REST API",
			DatabaseType: "Document database",
			ExampleStack: map[string]any{
				"frontend": "Next.js / React", "backend": "Next.js API routes",
				"database": "MongoDB", "auth": authStack(auth),
			},
		},
		Auth: authRequirement(auth, policy, len(roles), len(public) > 0),

		Roles:          roles,
		PublicPages:    public,
		ProtectedPages: protected,
		MainModules:    modulesFor(plan, tables),

		DatabaseDesign: DatabaseDesign{Tables: tables, Relationships: relationships},
		APIDesign:      apiFromTables(tables, auth, policy, plan, public),

		FunctionalRequirements:    frs,
		NonFunctionalRequirements: qualityAttributes(auth, devices),
		BusinessWorkflows:         workflowsFromPlan(plan),
		ValidationRules:           []ValidationRule{},
		NotificationRules:         []Notification{},
		SecurityRequirements:      securityFor(auth),
		UIUX: map[string]any{
			"design_style":        strings.TrimSpace(plan.LookAndFeel),
			"theme":               branding["theme"],
			"mobile_support":      !containsString(devices, "desktop") || len(devices) > 1,
			"pwa_support":         containsString(devices, "pwa"),
			"required_components": componentsFor(tables, auth, archetype),
		},
		ReportingRequirements:   []Reporting{},
		IntegrationRequirements: []Integration{},

		Assumptions: plan.Assumptions,
		Constraints: []string{"The system is built only from the approved plan; " +
			"anything outside it is a change request."},
		Ambiguities:        ambiguitiesFrom(plan),
		RiskPriority:       []Risk{},
		AcceptanceCriteria: acceptanceFrom(plan),
		Diagrams:           []Diagram{},

		Branding:         branding,
		ApprovedPlan:     asDoc(plan),
		RoleAccessMatrix: []RoleAccess{},
	}
	if auth {
		doc.RoleAccessMatrix = roleMatrix(roles, plan, public, protected)
	}
	doc.Traceability = traceability(frs, tables, append(append([]Page{}, public...), protected...))
	return doc
}

// AuthOn is whether this app has accounts at all. A plan whose only user is a
// visitor has none, whatever the app type usually does.
func AuthOn(pack *Pack, plan *Plan) bool {
	if pack != nil && pack.AuthPolicy == "disabled" {
		return false
	}
	if plan == nil {
		return true
	}
	for _, u := range plan.Users {
		key := strings.ToLower(strings.TrimSpace(u.Role))
		if key != "" && !anonymousRoles[key] && key != "user" {
			return true
		}
	}
	return false
}

func authStack(auth bool) string {
	if auth {
		return "Email + password session"
	}
	return "None"
}

// authRequirement is what the builder reads to decide whether to wire sign-in
// at all, and on what terms.
func authRequirement(auth bool, policy *AccountPolicy, roleCount int, hasPublic bool) Auth {
	if !auth {
		return Auth{LoginRequired: false, SelfRegistration: false}
	}
	open := policy.RegistrationMode == RegistrationOpen
	out := Auth{
		LoginRequired:      true,
		SignInRoute:        firstNonEmpty(policy.SignInRoute, "/login"),
		IdentityFields:     policy.SignInFields,
		RegistrationFields: policy.RegistrationFields,
		RegistrationMode:   firstNonEmpty(policy.RegistrationMode, RegistrationAdmin),
		SelfRegistration:   open,

		ProvisioningRole:          policy.ProvisioningRole,
		AccountManagementRoute:    policy.AccountManagementRoute,
		InvitationManagementRoute: policy.InvitationManagementRoute,
		InvitationAcceptRoute:     policy.InvitationAcceptRoute,
		RequestAccessRoute:        policy.RequestAccessRoute,
		AccessReviewRoute:         policy.AccessReviewRoute,

		PasswordResetRequired:    policy.PasswordResetRequired,
		SignedOutProtectedAccess: "redirect_to_sign_in",
		WrongRoleAccess:          "forbidden",
		AuthTransport:            "provider_managed",
	}
	if len(out.IdentityFields) == 0 {
		out.IdentityFields = []string{"email", "password"}
	}
	if len(out.RegistrationFields) == 0 {
		out.RegistrationFields = out.IdentityFields
	}
	out.RegistrationRoles = []string{}
	if open {
		out.SignUpRoute, out.RegistrationRole = policy.SignUpRoute, policy.RegistrationRole
		if out.RegistrationRole != "" {
			out.RegistrationRoles = []string{out.RegistrationRole}
		}
	}
	_ = roleCount
	_ = hasPublic
	return out
}

// --- roles, tables, pages ----------------------------------------------------------

// rolesFromPlan is one role per plan user, described by what they said they do.
func rolesFromPlan(plan *Plan) []Role {
	out := []Role{}
	seen := map[string]bool{}
	for _, user := range plan.Users {
		name := strings.TrimSpace(user.Role)
		key := snakeName(name)
		if key == "" || seen[key] {
			continue
		}
		seen[key] = true
		duties := asList(user.CanDo)
		description := strings.Join(duties, "; ")
		if description == "" {
			description = "Uses " + strings.ToLower(name) + " features of the system"
		}
		out = append(out, Role{RoleKey: key, RoleName: sentenceCase(name),
			Description: sentence(description)})
	}
	if len(out) == 0 {
		out = append(out, Role{RoleKey: "user", RoleName: "User", Description: "Uses the application."})
	}
	return out
}

func roleNames(roles []Role) []string {
	out := make([]string, 0, len(roles))
	for _, r := range roles {
		out = append(out, r.RoleName)
	}
	return out
}

// typeHints guess a column type from the label the customer wrote. They are
// only hints: getting one wrong costs a column type, not a column.
var typeHints = []struct {
	needles []string
	kind    string
}{
	{[]string{"price", "amount", "total", "cost", "rate", "salary", "fee", "discount", "balance", "subtotal"}, "decimal"},
	{[]string{"quantity", "qty", "stock", "count", "level", "number of", "age"}, "integer"},
	{[]string{"date of", "birth", "expiry", "due date", "preferred date"}, "date"},
	// " at " is spaced deliberately: as a bare substring it matched status,
	// category and location, and shipped them as datetime columns.
	{[]string{"created", "updated", " at ", "_at ", "time", "timestamp"}, "datetime"},
	{[]string{"is ", "has ", "active", "enabled", "paid", "confirmed"}, "boolean"},
	{[]string{"status", "state", "type", "category level"}, "enum"},
	{[]string{"description", "notes", "message", "body", "address", "comment"}, "text"},
	{[]string{"email"}, "string"},
	{[]string{"photo", "image", "picture", "file", "attachment", "logo"}, "string"},
}

func fieldType(label string) string {
	low := " " + strings.ToLower(strings.TrimSpace(label)) + " "
	for _, hint := range typeHints {
		for _, needle := range hint.needles {
			if strings.Contains(low, needle) {
				return hint.kind
			}
		}
	}
	return "string"
}

// authTables are the two tables accounts need. They belong to the composer, not
// to the plan, which is why the plan never has to name them.
func authTables() []Table {
	no, yes := false, true
	return []Table{
		{
			TableName: "users", Description: "Login accounts for all system users.",
			Fields: []Field{
				{Name: "id", Type: "uuid", PrimaryKey: true, Nullable: &no},
				{Name: "full_name", Type: "string", Nullable: &no},
				{Name: "email", Type: "string", Unique: true, Nullable: &no},
				{Name: "phone", Type: "string", Nullable: &yes},
				{Name: "password_hash", Type: "string", Nullable: &no},
				{Name: "role_id", Type: "foreign_key", References: "roles.id", Nullable: &no},
				{Name: "status", Type: "enum", Values: []any{"active", "inactive", "blocked"}, Default: "active", Nullable: &no},
				{Name: "last_login_at", Type: "datetime", Nullable: &yes},
				{Name: "created_at", Type: "datetime", Nullable: &no},
			},
		},
		{
			TableName: "roles", Description: "System roles for role-based access.",
			Fields: []Field{
				{Name: "id", Type: "uuid", PrimaryKey: true, Nullable: &no},
				{Name: "role_key", Type: "string", Unique: true, Nullable: &no},
				{Name: "role_name", Type: "string", Nullable: &no},
				{Name: "description", Type: "text", Nullable: &yes},
			},
		},
	}
}

// tablesFromPlan builds the schema from the plan's records alone, then joins
// up anything whose field name names another table.
func tablesFromPlan(plan *Plan, auth bool) ([]Table, []Relationship) {
	no := false
	tables := []Table{}
	for _, rec := range plan.Records {
		name := strings.TrimSpace(rec.Name)
		if name == "" {
			continue
		}
		table := tableName(name)
		if hasTable(tables, table) {
			continue
		}
		fields := []Field{{Name: "id", Type: "uuid", PrimaryKey: true, Nullable: &no}}
		for _, keep := range rec.Keeps {
			label := strings.TrimSpace(keep)
			fname := snakeName(label)
			if fname == "" || hasField(fields, fname) {
				continue
			}
			entry := Field{Name: fname, Type: fieldType(label), Nullable: &no}
			if entry.Type == "enum" {
				entry.Values = []any{"active", "inactive"}
				entry.Default = "active"
			}
			fields = append(fields, entry)
		}
		fields = append(fields, Field{Name: "created_at", Type: "datetime", Nullable: &no})
		tables = append(tables, Table{TableName: table,
			Description: sentenceCase(name) + " records.", Fields: fields})
	}

	relationships := []Relationship{}
	if auth {
		tables = append(authTables(), tables...)
		relationships = append(relationships, Relationship{
			From: "users.role_id", To: "roles.id", Type: "many_to_one",
			Description: "Each user belongs to one role.",
		})
	}

	seen := map[string]bool{}
	for _, r := range relationships {
		seen[r.From+"→"+r.To] = true
	}
	for i := range tables {
		if authTableNames[tables[i].TableName] {
			continue
		}
		for j := range tables[i].Fields {
			f := &tables[i].Fields[j]
			if f.PrimaryKey || !strings.HasSuffix(f.Name, "_id") {
				continue
			}
			target := tableName(strings.TrimSuffix(f.Name, "_id"))
			if target == tables[i].TableName || !hasTable(tables, target) {
				continue
			}
			f.Type, f.References = "foreign_key", target+".id"
			edge := tables[i].TableName + "." + f.Name + "→" + target + ".id"
			if seen[edge] {
				continue
			}
			seen[edge] = true
			relationships = append(relationships, Relationship{
				From: tables[i].TableName + "." + f.Name, To: target + ".id", Type: "many_to_one",
				Description: "Each " + singularLabel(tables[i].TableName) + " refers to one " +
					singularLabel(target) + ".",
			})
		}
	}
	return tables, relationships
}

func hasTable(tables []Table, name string) bool {
	for _, t := range tables {
		if t.TableName == name {
			return true
		}
	}
	return false
}

func hasField(fields []Field, name string) bool {
	for _, f := range fields {
		if f.Name == name {
			return true
		}
	}
	return false
}

// pagesFromPlan turns approved screens into exact routes, split by whether
// signing in is needed to open them.
func pagesFromPlan(plan *Plan, auth bool, policy *AccountPolicy) (public, protected []Page) {
	yes, no := true, false
	public, protected = []Page{}, []Page{}
	seen := map[string]bool{}

	for i, screen := range plan.Screens {
		name := strings.TrimSpace(screen.Name)
		if name == "" {
			continue
		}
		route := strings.TrimSpace(screen.Route)
		if route == "" {
			route = routeFor(name, i)
		}
		if !strings.HasPrefix(route, "/") {
			route = "/" + route
		}
		if seen[route] {
			continue
		}
		seen[route] = true

		who := asList(screen.Who)
		page := Page{
			PageName: sentenceCase(name), Route: route, PageType: "page",
			Sections: []string{}, Functions: []string{},
		}
		if purpose := sentence(screen.Purpose); purpose != "" {
			page.Functions = []string{purpose}
		}

		anonymous := len(who) == 0
		if !anonymous {
			anonymous = true
			for _, w := range who {
				if !anonymousRoles[strings.ToLower(w)] {
					anonymous = false
					break
				}
			}
		}
		if !auth || anonymous {
			page.LoginRequired = &no
			public = append(public, page)
			continue
		}
		page.LoginRequired = &yes
		for _, w := range who {
			page.AllowedRoles = append(page.AllowedRoles, snakeName(w))
		}
		protected = append(protected, page)
	}
	if !auth {
		return public, protected
	}

	signIn := firstNonEmpty(policy.SignInRoute, "/login")
	if !seen[signIn] {
		seen[signIn] = true
		public = append(public, Page{
			PageName: "Sign In", Route: signIn, PageType: "auth", LoginRequired: &no,
			Sections: []string{}, Functions: []string{"Sign in with the approved identity fields."},
		})
	}
	if policy.RegistrationMode == RegistrationOpen {
		signUp := firstNonEmpty(policy.SignUpRoute, "/register")
		if !seen[signUp] {
			seen[signUp] = true
			public = append(public, Page{
				PageName: "Sign Up", Route: signUp, PageType: "auth", LoginRequired: &no,
				Sections: []string{},
				Functions: []string{"Create the approved " +
					firstNonEmpty(policy.RegistrationRole, "user") + " account."},
			})
		}
	}

	extraPublic, extraProtected := accountPageSpecs(policy)
	for _, page := range extraPublic {
		if !seen[page.Route] {
			seen[page.Route] = true
			public = append(public, page)
		}
	}
	for _, page := range extraProtected {
		if !seen[page.Route] {
			seen[page.Route] = true
			protected = append(protected, page)
		}
	}
	return public, protected
}

func routeFor(name string, index int) string {
	if index == 0 {
		return "/"
	}
	slug := strings.ReplaceAll(snakeName(name), "_", "-")
	if slug == "" {
		return "/page-" + itoa(index)
	}
	return "/" + slug
}

// --- requirements ------------------------------------------------------------------

// requirementsFromPlan writes one requirement per thing the plan promised, and
// nothing else. Every entry can be pointed back at a line the customer read.
func requirementsFromPlan(plan *Plan, tables []Table, auth bool, archetype string,
	policy *AccountPolicy, publicPages []Page) []Requirement {

	out := []Requirement{}
	seen := map[string]bool{}
	add := func(module, text, priority string, roles []string) {
		text = sentence(text)
		key := strings.ToLower(text)
		if text == "" || seen[key] {
			return
		}
		seen[key] = true
		if roles == nil {
			roles = []string{}
		}
		out = append(out, Requirement{
			ID: requirementID("FR", len(out)+1), Module: module,
			Requirement: text, Priority: priority, AllowedRoles: roles,
		})
	}

	if auth {
		add("Authentication", "The system shall let a person sign in with an email address and password", "high", nil)
		add("Authorization", "The system shall show each signed-in person only the screens their role allows", "high", nil)
		if policy.RegistrationMode == RegistrationOpen {
			role := firstNonEmpty(policy.RegistrationRole, "approved default user")
			add("Authentication", "The system shall let a visitor create only a "+role+
				" account from public sign-up", "high", nil)
		} else {
			add("Authentication", "The system shall not expose public self-registration", "high", nil)
		}
		for _, spec := range accountRequirementSpecs(policy) {
			add(spec.Module, spec.Text, spec.Priority, spec.Roles)
		}
	}

	for _, user := range plan.Users {
		role := sentenceCase(firstNonEmpty(user.Role, "user"))
		for _, duty := range user.CanDo {
			if text := lowerFirst(duty); text != "" {
				add(role, "The system shall allow "+role+" users to "+text, "high",
					[]string{snakeName(role)})
			}
		}
	}

	for i, feature := range plan.Features {
		feature = strings.TrimRight(strings.TrimSpace(feature), ".")
		if feature == "" {
			continue
		}
		before := len(out)
		if strings.HasPrefix(strings.ToLower(feature), "the system") {
			add("Core Features", feature, "high", nil)
		} else {
			add("Core Features", "The system shall provide "+lowerFirst(feature), "high", nil)
		}
		// Provenance is recorded where it is known rather than guessed at
		// later by matching the requirement's wording back to the feature.
		if len(out) > before {
			out[len(out)-1].Source = "Approved Plan Feature " + itoa(i+1)
		}
	}

	if archetype == archetypeCRUD {
		for _, table := range tables {
			if authTableNames[table.TableName] {
				continue
			}
			label := strings.ReplaceAll(table.TableName, "_", " ")
			singular := singularLabel(label)
			module := sentenceCase(label)
			actions := roleActionsForTable(plan, table.TableName)
			publicRead := publicReadsTable(publicPages, table.TableName)

			readText := "The system shall let approved users view " + label
			if publicRead {
				readText = "The system shall let visitors view " + label
			}
			specs := []struct {
				action, text, priority string
			}{
				{"read", readText, "high"},
				{"create", "The system shall let approved users create one " + singular + " record", "high"},
				{"update", "The system shall let approved users update one " + singular + " record", "medium"},
				{"delete", "The system shall let approved users delete one " + singular + " record", "medium"},
			}
			for _, spec := range specs {
				granted := len(actions[spec.action]) > 0
				isPublic := spec.action == "read" && publicRead
				if !granted && !isPublic {
					continue
				}
				roles := actions[spec.action]
				if isPublic {
					roles = nil
				}
				add(module, spec.text, spec.priority, roles)
			}
		}
	}

	for _, wf := range plan.Workflows {
		name := strings.TrimSpace(wf.Name)
		if name == "" || len(wf.Steps) == 0 {
			continue
		}
		var roles []string
		if who := snakeName(wf.Who); who != "" {
			roles = []string{who}
		}
		add("Workflows", "The system shall support the "+strings.ToLower(name)+
			" workflow end to end", "medium", roles)
	}

	for len(out) < 3 {
		add("Core Features", "The system shall do what the approved plan describes ("+
			itoa(len(out)+1)+")", "medium", nil)
	}
	return out
}

// qualityAttributes are the non-functional requirements. The universal ones are
// not scope, so they are always present; the account ones are gated on there
// being accounts to secure.
func qualityAttributes(auth bool, devices []string) []NonFunctional {
	items := [][2]string{
		{"Performance", "Primary screens should load within 3 seconds (P95) under normal conditions."},
		{"Usability", "A first-time user should be able to complete the main task without training."},
		{"Compatibility", "The application must work in current versions of Chrome, Edge, Firefox and Safari."},
		{"Maintainability", "Code shall be organised by feature, so one change touches one place."},
	}
	if !containsString(devices, "desktop") || len(devices) > 1 {
		items = append(items, [2]string{"Usability",
			"The interface must be usable on a phone screen without horizontal scrolling."})
	}
	if auth {
		items = append(items,
			[2]string{"Security", "Passwords must be hashed with a strong adaptive algorithm (bcrypt or argon2)."},
			[2]string{"Security", "Every protected screen and endpoint must check both sign-in and role."})
	}
	out := make([]NonFunctional, 0, len(items))
	for i, item := range items {
		out = append(out, NonFunctional{
			ID: requirementID("NFR", i+1), Category: item[0], Requirement: item[1],
		})
	}
	return out
}

func securityFor(auth bool) []string {
	out := []string{
		"All traffic must be served over HTTPS.",
		"Input from the browser must be validated on the server before it is stored.",
	}
	if auth {
		out = append(out,
			"Passwords must never be stored or logged in plain text.",
			"A session must expire and be re-authenticated after a period of inactivity.",
			"Each request to a protected endpoint must be checked against the caller's role.")
	}
	return out
}

// componentsFor is the UI this app actually needs, not the standard admin kit.
func componentsFor(tables []Table, auth bool, archetype string) []string {
	out := []string{"Navigation bar"}
	domain := 0
	for _, t := range tables {
		if !authTableNames[t.TableName] {
			domain++
		}
	}
	if domain > 0 && archetype == archetypeCRUD {
		out = append(out, "Data table with search and filter", "Create / edit form",
			"Confirmation dialog", "Empty state")
	}
	if auth {
		out = append(out, "Sign-in form", "Signed-in user menu")
	}
	switch archetype {
	case archetypeLanding:
		out = append(out, "Hero section", "Section headings", "Enquiry form")
	case archetypeTool:
		out = append(out, "Input panel", "Result display")
	}
	return out
}

func modulesFor(plan *Plan, tables []Table) []string {
	out := []string{}
	for _, screen := range plan.Screens {
		if name := sentenceCase(screen.Name); name != "" && !containsString(out, name) {
			out = append(out, name)
		}
	}
	for _, t := range tables {
		name := sentenceCase(strings.ReplaceAll(t.TableName, "_", " "))
		if name != "" && !containsString(out, name) {
			out = append(out, name)
		}
	}
	return out
}

func workflowsFromPlan(plan *Plan) []Workflow {
	out := []Workflow{}
	for _, w := range plan.Workflows {
		if len(w.Steps) == 0 {
			continue
		}
		out = append(out, Workflow{
			WorkflowName: firstNonEmpty(w.Name, "Workflow"), Who: w.Who, Steps: w.Steps,
		})
	}
	return out
}

// ambiguitiesFrom carries the plan's unsettled questions into the document, so
// what nobody decided stays visible rather than being quietly decided.
func ambiguitiesFrom(plan *Plan) []Ambiguity {
	out := []Ambiguity{}
	for _, q := range plan.OpenQuestions {
		if strings.TrimSpace(q.Question) == "" {
			continue
		}
		out = append(out, Ambiguity{
			ID: requirementID("AMB", len(out)+1), Area: "Approved plan",
			Description: q.Question, AssumptionMade: "Left open; to be settled before build.",
			NeedsClarification: q.Required,
		})
	}
	return out
}

func acceptanceFrom(plan *Plan) []Acceptance {
	out := []Acceptance{}
	for _, f := range plan.Features {
		if c := sentence(f); c != "" {
			out = append(out, Acceptance{ID: requirementID("AC", len(out)+1), Criterion: c})
		}
	}
	return out
}

// --- branding ------------------------------------------------------------------------

// paletteAliases map the names the interview offers onto the palettes we hold.
var paletteAliases = map[string]string{
	"corporate_blue": "business", "corporate": "business", "blue": "business",
	"fresh_mint": "mint", "green": "mint",
	"deep_navy_gold": "navy", "deep_navy": "navy",
	"black_gold": "gold", "black": "gold",
	"modern_indigo": "indigo", "purple": "indigo",
}

// buildBranding is what the app looks like, and whether a logo has to be drawn.
func buildBranding(session *Session, pack *Pack, appName string) map[string]any {
	logoWanted := false
	for _, item := range asList(answerOf(session, "image_kinds")) {
		switch strings.ToLower(strings.TrimSpace(item)) {
		case "logo", "logo_mark", "logo mark":
			logoWanted = true
		}
	}
	source := "none"
	if logoWanted {
		source = "generate"
	}

	name := resolvePalette(answerOf(session, "color_palette"), pack)
	palette := Knowledge().Palettes[name]
	theme := firstText(answerOf(session, "theme_type"), "light")

	branding := map[string]any{
		"logo_required": source == "generate", "logo_source": source,
		"logo_image_prompt": "", "theme": theme, "palette": name,
		"primary_color": firstNonEmpty(palette["primary"], "#6366F1"),
	}
	if source == "generate" {
		branding["logo_image_prompt"] = logoPrompt(appName, pack, branding)
	}
	return branding
}

func resolvePalette(answer any, pack *Pack) string {
	key := ""
	if list := asList(answer); len(list) > 0 {
		key = snakeName(list[0])
	}
	palettes := Knowledge().Palettes
	if _, ok := palettes[key]; ok {
		return key
	}
	if alias, ok := paletteAliases[key]; ok {
		return alias
	}
	fallback := "indigo"
	if pack != nil {
		fallback = firstNonEmpty(pack.PaletteName, "indigo")
	}
	if _, ok := palettes[fallback]; ok {
		return fallback
	}
	return "indigo"
}

func logoPrompt(appName string, pack *Pack, branding map[string]any) string {
	label := strings.ToLower(firstNonEmpty(pack.AppLabel, "web application"))
	subject := "a " + label
	if pack.DomainLabel != "" && pack.DomainConfidence >= DomainFloor {
		subject += " for " + strings.ToLower(pack.DomainLabel)
	}
	background := "white"
	if branding["theme"] == "dark" {
		background = "dark charcoal"
	}
	return "A flat vector logo mark for \"" + appName + "\", " + subject + ". " +
		"One simple, memorable symbol that reads clearly at 32 pixels. " +
		"Primary colour " + firstText(branding["primary_color"]) + " on a " + background + " background. " +
		"No text, no lettering, no gradients, no photorealism, no drop shadows. " +
		"Square composition, generous margin, transparent background, SVG-like clean edges."
}
