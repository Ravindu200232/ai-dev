package srs

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
)

// Who may do what, derived from the approved plan rather than assumed. Every
// permission here is read off something the customer signed: a duty they gave
// a role, a step in a journey, or a page they said was public. Nothing grants
// itself by default, because a default grant is how an admin screen ends up
// open to everyone.

// authTableNames are the two tables accounts need. They are added by the
// composer, never by the plan, so they are excluded wherever the plan's own
// records are being walked.
var authTableNames = map[string]bool{"users": true, "roles": true}

// crudVerbs map the words people actually write into the four actions.
var crudVerbs = []struct {
	action string
	words  []string
}{
	{"read", []string{"view", "see", "read", "browse", "list", "search", "filter", "review", "track"}},
	{"create", []string{"add", "create", "new", "place", "submit", "book", "purchase", "record"}},
	{"update", []string{"edit", "update", "change", "adjust", "set", "approve", "reject", "manage"}},
	{"delete", []string{"delete", "remove"}},
}

var wordToken = regexp.MustCompile(`[a-z0-9_]+`)

// roleActionsForTable reads CRUD permissions for one table out of the plan's
// role duties and journey steps.
func roleActionsForTable(plan *Plan, tableName string) map[string][]string {
	label := strings.ReplaceAll(tableName, "_", " ")
	terms := []string{label, singularLabel(label)}
	out := map[string][]string{}
	for _, v := range crudVerbs {
		out[v.action] = []string{}
	}

	add := func(role, text string) {
		low := strings.ToLower(text)
		mentioned := false
		for _, term := range terms {
			if term != "" && strings.Contains(low, term) {
				mentioned = true
				break
			}
		}
		if !mentioned {
			return
		}
		key := snakeName(role)
		if key == "" {
			return
		}
		tokens := wordToken.FindAllString(low, -1)
		// "manage" is every action at once, which is what people mean by it.
		managed := containsString(tokens, "manage") || strings.Contains(low, "manage")
		for _, v := range crudVerbs {
			hit := managed
			for _, word := range v.words {
				if hit {
					break
				}
				for _, token := range tokens {
					if strings.HasPrefix(token, word) {
						hit = true
						break
					}
				}
			}
			if hit && !containsString(out[v.action], key) {
				out[v.action] = append(out[v.action], key)
			}
		}
	}

	for _, user := range plan.Users {
		for _, duty := range user.CanDo {
			add(user.Role, duty)
		}
	}
	for _, flow := range plan.Workflows {
		for _, step := range flow.Steps {
			add(flow.Who, step)
		}
	}
	return out
}

// publicReadsTable is whether a page anyone can open already shows this table.
// If it does, reading it is not an authenticated action.
func publicReadsTable(pages []Page, tableName string) bool {
	label := strings.ReplaceAll(tableName, "_", " ")
	singular := singularLabel(label)
	for _, page := range pages {
		blob := strings.ToLower(strings.Join(append([]string{
			page.PageName, page.Route,
		}, append(page.Functions, page.Sections...)...), " "))
		if strings.Contains(blob, label) || (singular != "" && strings.Contains(blob, singular)) {
			return true
		}
	}
	return false
}

// apiFromTables writes one endpoint per action somebody was actually granted.
// A table nobody may touch gets no API at all.
func apiFromTables(tables []Table, auth bool, policy *AccountPolicy, plan *Plan, publicPages []Page) []Endpoint {
	api := []Endpoint{}
	if auth {
		api = append(api, accountAPISpecs(policy)...)
	}

	for _, table := range tables {
		name := table.TableName
		if authTableNames[name] {
			continue
		}
		resource := strings.ReplaceAll(name, "_", "-")
		singular := singularLabel(name)
		actions := roleActionsForTable(plan, name)
		publicRead := publicReadsTable(publicPages, name)

		methods := map[string][]Endpoint{
			"read": {
				{Method: "GET", Path: "/api/" + resource, Description: "List " + name + "."},
				{Method: "GET", Path: "/api/" + resource + "/{id}", Description: "Read one " + singular + " record."},
			},
			"create": {{Method: "POST", Path: "/api/" + resource, Description: "Create one " + singular + " record."}},
			"update": {{Method: "PUT", Path: "/api/" + resource + "/{id}", Description: "Update one " + singular + " record."}},
			"delete": {{Method: "DELETE", Path: "/api/" + resource + "/{id}", Description: "Delete one " + singular + " record."}},
		}
		for _, action := range []string{"read", "create", "update", "delete"} {
			granted := len(actions[action]) > 0
			isPublic := action == "read" && publicRead
			if !granted && !isPublic {
				continue
			}
			for _, row := range methods[action] {
				needsAuth := auth && !isPublic
				row.AuthRequired = &needsAuth
				if granted && !isPublic {
					row.AllowedRoles = actions[action]
				}
				api = append(api, row)
			}
		}
	}
	return api
}

// roleMatrix is what each role may open and do, read off the plan.
func roleMatrix(roles []Role, plan *Plan, public, protected []Page) []RoleAccess {
	duties := map[string][]string{}
	for _, u := range plan.Users {
		duties[snakeName(u.Role)] = u.CanDo
	}

	out := make([]RoleAccess, 0, len(roles))
	for _, r := range roles {
		allowed, restricted := []string{}, []string{}
		for _, p := range protected {
			if containsString(p.AllowedRoles, r.RoleKey) {
				allowed = append(allowed, p.PageName)
			} else {
				restricted = append(restricted, p.PageName)
			}
		}
		for _, p := range public {
			allowed = append(allowed, p.PageName)
		}
		out = append(out, RoleAccess{
			Role: r.RoleKey, AllowedPages: allowed, RestrictedPages: restricted,
			AllowedFunctions: duties[r.RoleKey], RestrictedFunctions: []string{},
		})
	}
	return out
}

var pageWord = regexp.MustCompile(`[a-z0-9]{4,}`)

// traceability maps each requirement to the pages and records it touches, so a
// requirement nothing implements is visible as an empty row rather than an
// absence nobody notices.
func traceability(frs []Requirement, tables []Table, pages []Page) []Trace {
	out := make([]Trace, 0, len(frs))
	for _, fr := range frs {
		text := strings.ToLower(fr.Module + " " + fr.Requirement)
		roles := map[string]bool{}
		for _, r := range fr.AllowedRoles {
			if key := snakeName(r); key != "" {
				roles[key] = true
			}
		}

		var matchedTables []string
		for _, t := range tables {
			label := strings.ReplaceAll(t.TableName, "_", " ")
			if strings.Contains(text, label) || strings.Contains(text, singularLabel(label)) {
				matchedTables = append(matchedTables, t.TableName)
			}
		}

		var matchedPages []string
		for _, page := range pages {
			nameMatch := false
			for _, word := range pageWord.FindAllString(strings.ToLower(page.PageName), -1) {
				if strings.Contains(text, word) {
					nameMatch = true
					break
				}
			}
			roleMatch := false
			for _, r := range page.AllowedRoles {
				if roles[snakeName(r)] {
					roleMatch = true
					break
				}
			}
			if !nameMatch && !roleMatch {
				continue
			}
			if label := firstNonEmpty(page.PageName, page.Route); label != "" && !containsString(matchedPages, label) {
				matchedPages = append(matchedPages, label)
			}
		}

		out = append(out, Trace{
			RequirementID: fr.ID, Module: fr.Module,
			Pages: matchedPages, Tables: matchedTables,
			TestCase: "TC-" + strings.TrimPrefix(fr.ID, "FR-"),
		})
	}
	return out
}

// --- accounts ----------------------------------------------------------------------
//
// Each registration mode brings its own pages, requirements and endpoints. They
// are listed once here so a mode cannot be half-implemented: a policy that says
// "invite" and a spec with no way to accept one is a spec nobody can build.

// accountPageSpecs are the extra pages a registration mode needs.
func accountPageSpecs(policy *AccountPolicy) (public, protected []Page) {
	yes, no := true, false
	role := provisioningKey(policy)

	switch policy.RegistrationMode {
	case RegistrationAdmin:
		protected = append(protected, Page{
			PageName: "User Management",
			Route:    firstNonEmpty(policy.AccountManagementRoute, "/admin/users"),
			PageType: "account_management", LoginRequired: &yes,
			AllowedRoles: []string{role}, Sections: []string{},
			Functions: []string{"List user accounts.", "Create user accounts.",
				"Set an approved role on each account."},
		})
	case RegistrationInvite:
		protected = append(protected, Page{
			PageName: "Invitations",
			Route:    firstNonEmpty(policy.InvitationManagementRoute, "/admin/invitations"),
			PageType: "account_management", LoginRequired: &yes,
			AllowedRoles: []string{role}, Sections: []string{},
			Functions: []string{"Send account invitations.", "View invitation status."},
		})
		public = append(public, Page{
			PageName: "Accept Invitation",
			Route:    firstNonEmpty(policy.InvitationAcceptRoute, "/accept-invite"),
			PageType: "auth", LoginRequired: &no, Sections: []string{},
			Functions: []string{"Accept a valid invitation and create the invited account."},
		})
	case RegistrationRequest:
		public = append(public, Page{
			PageName: "Request Access",
			Route:    firstNonEmpty(policy.RequestAccessRoute, "/request-access"),
			PageType: "auth", LoginRequired: &no, Sections: []string{},
			Functions: []string{"Submit an access request."},
		})
		protected = append(protected, Page{
			PageName: "Access Requests",
			Route:    firstNonEmpty(policy.AccessReviewRoute, "/admin/access-requests"),
			PageType: "account_management", LoginRequired: &yes,
			AllowedRoles: []string{role}, Sections: []string{},
			Functions: []string{"Review access requests.", "Approve or reject a request."},
		})
	}
	return public, protected
}

// accountRequirementSpec is one requirement a registration mode brings with it.
type accountRequirementSpec struct {
	Module   string
	Text     string
	Priority string
	Roles    []string
}

func accountRequirementSpecs(policy *AccountPolicy) []accountRequirementSpec {
	role := []string{provisioningKey(policy)}
	switch policy.RegistrationMode {
	case RegistrationAdmin:
		return []accountRequirementSpec{
			{"Authentication", "The system shall let the provisioning role create user accounts", "high", role},
		}
	case RegistrationInvite:
		return []accountRequirementSpec{
			{"Authentication", "The system shall let the provisioning role send account invitations", "high", role},
			{"Authentication", "The system shall let an invited person accept a valid invitation and create the invited account", "high", nil},
		}
	case RegistrationRequest:
		return []accountRequirementSpec{
			{"Authentication", "The system shall let a visitor request access", "high", nil},
			{"Authentication", "The system shall let the provisioning role approve or reject access requests", "high", role},
		}
	}
	return nil
}

func accountAPISpecs(policy *AccountPolicy) []Endpoint {
	yes, no := true, false
	role := []string{provisioningKey(policy)}
	switch policy.RegistrationMode {
	case RegistrationAdmin:
		return []Endpoint{
			{Method: "GET", Path: "/api/users", Description: "List user accounts.", AuthRequired: &yes, AllowedRoles: role},
			{Method: "POST", Path: "/api/users", Description: "Create a user account with an approved role.", AuthRequired: &yes, AllowedRoles: role},
		}
	case RegistrationInvite:
		return []Endpoint{
			{Method: "GET", Path: "/api/invitations", Description: "List account invitations.", AuthRequired: &yes, AllowedRoles: role},
			{Method: "POST", Path: "/api/invitations", Description: "Send an account invitation.", AuthRequired: &yes, AllowedRoles: role},
			{Method: "POST", Path: "/api/auth/accept-invite", Description: "Accept a valid invitation and create the invited account.", AuthRequired: &no},
		}
	case RegistrationRequest:
		return []Endpoint{
			{Method: "POST", Path: "/api/access-requests", Description: "Submit an access request.", AuthRequired: &no},
			{Method: "GET", Path: "/api/access-requests", Description: "List access requests.", AuthRequired: &yes, AllowedRoles: role},
			{Method: "PUT", Path: "/api/access-requests/{id}", Description: "Approve or reject an access request.", AuthRequired: &yes, AllowedRoles: role},
		}
	}
	return nil
}

func provisioningKey(policy *AccountPolicy) string {
	if policy == nil {
		return "admin"
	}
	if key := snakeName(policy.ProvisioningRole); key != "" {
		return key
	}
	return "admin"
}

// normalizeAccountPolicy fills in the mechanics a mode needs without widening
// it. It is the last place a privileged role can be caught before it becomes
// something a public form hands out.
func normalizeAccountPolicy(policy *AccountPolicy, roles []Role) *AccountPolicy {
	if policy == nil || !policy.AccountsRequired {
		return &AccountPolicy{AccountsRequired: false, RegistrationMode: RegistrationNone}
	}
	out := *policy
	if out.RegistrationMode == "" {
		out.RegistrationMode = RegistrationAdmin
	}
	out.SignInRoute = firstNonEmpty(out.SignInRoute, "/login")
	if len(out.SignInFields) == 0 {
		out.SignInFields = []string{"email", "password"}
	}
	if len(out.RegistrationFields) == 0 {
		out.RegistrationFields = append([]string{}, out.SignInFields...)
	}

	name := func(r Role) string { return strings.TrimSpace(firstNonEmpty(r.RoleName, r.RoleKey)) }

	if out.RegistrationMode == RegistrationOpen {
		chosen := strings.TrimSpace(out.RegistrationRole)
		if chosen == "" || !safeSignupRole(chosen) {
			chosen = ""
			for _, r := range roles {
				if n := name(r); n != "" && safeSignupRole(n) {
					chosen = n
					break
				}
			}
		}
		if chosen != "" {
			out.RegistrationRole = chosen
			out.SignUpRoute = firstNonEmpty(out.SignUpRoute, "/register")
		} else {
			// Nobody unprivileged to sign up as, so sign-up closes rather than
			// handing out the only role there is.
			out.RegistrationMode, out.RegistrationRole, out.SignUpRoute = RegistrationAdmin, "", ""
		}
	}

	switch out.RegistrationMode {
	case RegistrationAdmin, RegistrationInvite, RegistrationRequest:
		if strings.TrimSpace(out.ProvisioningRole) == "" {
			for _, r := range roles {
				if n := name(r); n != "" && !safeSignupRole(n) {
					out.ProvisioningRole = n
					break
				}
			}
		}
		if strings.TrimSpace(out.ProvisioningRole) == "" && len(roles) > 0 {
			out.ProvisioningRole = name(roles[0])
		}
	}

	switch out.RegistrationMode {
	case RegistrationAdmin:
		out.AccountManagementRoute = firstNonEmpty(out.AccountManagementRoute, "/admin/users")
	case RegistrationInvite:
		out.InvitationManagementRoute = firstNonEmpty(out.InvitationManagementRoute, "/admin/invitations")
		out.InvitationAcceptRoute = firstNonEmpty(out.InvitationAcceptRoute, "/accept-invite")
	case RegistrationRequest:
		out.RequestAccessRoute = firstNonEmpty(out.RequestAccessRoute, "/request-access")
		out.AccessReviewRoute = firstNonEmpty(out.AccessReviewRoute, "/admin/access-requests")
	}
	return &out
}

// --- naming ------------------------------------------------------------------------

// snakeName is the identifier form of a label: `Sale Item` and `saleItem` both
// become `sale_item`. Unlike the interview's own version it has no fallback,
// because an empty name here means "no role", not "some role".
func snakeName(text string) string {
	var b strings.Builder
	runes := []rune(strings.TrimSpace(text))
	for i, r := range runes {
		if i > 0 && r >= 'A' && r <= 'Z' {
			prev := runes[i-1]
			if (prev >= 'a' && prev <= 'z') || (prev >= '0' && prev <= '9') {
				b.WriteByte('_')
			}
		}
		b.WriteRune(r)
	}
	return strings.Trim(nonWord.ReplaceAllString(strings.ToLower(b.String()), "_"), "_")
}

// tableName is the plural snake_case a record is stored under. The builder
// creates the collection under exactly this name, so it is worth getting right.
func tableName(text string) string {
	name := snakeName(text)
	if name == "" {
		return "record"
	}
	if strings.HasSuffix(name, "y") {
		switch {
		case strings.HasSuffix(name, "ay"), strings.HasSuffix(name, "ey"),
			strings.HasSuffix(name, "oy"), strings.HasSuffix(name, "uy"):
		default:
			return name[:len(name)-1] + "ies"
		}
	}
	for _, suffix := range []string{"s", "x", "z", "ch", "sh"} {
		if strings.HasSuffix(name, suffix) {
			return name + "es"
		}
	}
	return name + "s"
}

// singularLabel drops one plural ending, which is all the API paths and
// requirement wording need.
func singularLabel(label string) string {
	if strings.HasSuffix(label, "s") {
		return label[:len(label)-1]
	}
	return label
}

// sentence is one requirement or purpose, ending in exactly one full stop.
func sentence(text string) string {
	text = strings.TrimRight(strings.TrimSpace(text), ".")
	if text == "" {
		return ""
	}
	return text + "."
}

// lowerFirst turns a duty into something that reads after "shall allow X to".
func lowerFirst(text string) string {
	text = strings.TrimRight(strings.TrimSpace(text), ".")
	if text == "" {
		return ""
	}
	return strings.ToLower(text[:1]) + text[1:]
}

// requirementID is the zero-padded id form every requirement, ambiguity and
// acceptance criterion uses. The builder parses these, so the shape is fixed.
func requirementID(prefix string, n int) string {
	digits := itoa(n)
	for len(digits) < 3 {
		digits = "0" + digits
	}
	return prefix + "-" + digits
}

func itoa(n int) string { return strconv.Itoa(n) }

// asDoc and planFromDoc move a plan in and out of the document's untyped
// `approved_plan` field, which stays untyped so an older stored plan with a
// field we have since dropped still round-trips.
func asDoc(v any) map[string]any {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil
	}
	return out
}

func planFromDoc(body map[string]any) *Plan {
	var out Plan
	if len(body) == 0 {
		return &out
	}
	if raw, err := json.Marshal(body); err == nil {
		_ = json.Unmarshal(raw, &out)
	}
	return &out
}
