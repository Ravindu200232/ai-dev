package srs

import (
	"regexp"
	"strings"
)

// Two contracts sit on top of the handoff: who may hold an account and what
// each role may reach, and what each capability has to prove. The third — what
// has to be true before a test can run — is in testing.go, because it is built
// from these two.

// --- who may reach what ---------------------------------------------------------------

// AuthContract is the account rules, stated so literally that the builder has
// nothing left to decide.
type AuthContract struct {
	Enabled         bool   `json:"enabled"`
	SignIn          bool   `json:"sign_in"`
	PublicBehavior  string `json:"public_behavior,omitempty"`
	SignInRoute     string `json:"sign_in_route,omitempty"`
	SignOutRequired bool   `json:"sign_out_required,omitempty"`

	IdentityFields        []string `json:"identity_fields,omitempty"`
	RegistrationFields    []string `json:"registration_fields,omitempty"`
	PasswordResetRequired bool     `json:"password_reset_required,omitempty"`

	RegistrationMode  string   `json:"registration_mode,omitempty"`
	SelfRegistration  bool     `json:"self_registration"`
	SignUpRoute       string   `json:"sign_up_route,omitempty"`
	RegistrationRoles []string `json:"registration_roles,omitempty"`

	Provisioning *Provisioning `json:"provisioning,omitempty"`

	AuthTransport            string            `json:"auth_transport,omitempty"`
	RoleSource               string            `json:"role_source,omitempty"`
	SignedOutProtectedAccess string            `json:"signed_out_protected_access,omitempty"`
	WrongRoleAccess          string            `json:"wrong_role_access,omitempty"`
	RoleHome                 map[string]string `json:"role_home,omitempty"`
	Roles                    []RoleRule        `json:"roles,omitempty"`
	Rules                    []string          `json:"rules,omitempty"`
}

// Provisioning is how an account comes to exist when nobody can sign up.
type Provisioning struct {
	Mode                      string `json:"mode"`
	Role                      string `json:"role"`
	AccountManagementRoute    string `json:"account_management_route"`
	InvitationManagementRoute string `json:"invitation_management_route"`
	InvitationAcceptRoute     string `json:"invitation_accept_route"`
	RequestAccessRoute        string `json:"request_access_route"`
	AccessReviewRoute         string `json:"access_review_route"`
}

// RoleRule is one role's reach, as routes rather than page names, because a
// route is the thing the server actually has to check.
type RoleRule struct {
	Role                string   `json:"role"`
	AllowedRoutes       []string `json:"allowed_routes"`
	DeniedRoutes        []string `json:"denied_routes"`
	AllowedFunctions    []string `json:"allowed_functions"`
	RestrictedFunctions []string `json:"restricted_functions"`
	HomeRoute           string   `json:"home_route"`
}

func buildAuthContract(doc *Document, plan *Plan, pages []HandoffPage,
	roles []HandoffRole, access []RoleAccess, auth bool) AuthContract {

	if !auth {
		return AuthContract{Enabled: false, SignIn: false, SelfRegistration: false,
			PublicBehavior: "all pages are public"}
	}

	req := doc.Auth
	policy := plan.AccountPolicy
	if policy == nil {
		policy = &AccountPolicy{}
	}
	signIn := clean(firstNonEmpty(req.SignInRoute, policy.SignInRoute))
	if signIn == "" {
		signIn = matchRoute(pages, signInPage)
	}
	signUp := clean(firstNonEmpty(req.SignUpRoute, policy.SignUpRoute))
	if signUp == "" {
		signUp = matchRoute(pages, signUpPage)
	}

	mode := clean(firstNonEmpty(req.RegistrationMode, policy.RegistrationMode))
	if mode == "" {
		mode = RegistrationAdmin
		if signUp != "" {
			mode = RegistrationOpen
		}
	}
	self := req.SelfRegistration || (!doc.Auth.LoginRequired && mode == RegistrationOpen)
	if doc.Auth.LoginRequired {
		self = req.SelfRegistration
	}
	if !self {
		signUp = ""
	}

	registration := cleanList(req.RegistrationRoles)
	if one := clean(firstNonEmpty(req.RegistrationRole, policy.RegistrationRole)); one != "" &&
		!containsString(registration, one) {
		registration = append(registration, one)
	}
	if self && len(registration) == 0 {
		if safe := firstSafeRole(roles); safe != "" {
			registration = append(registration, safe)
		}
	}
	if self {
		// Last gate: a privileged role must never be reachable through a form.
		var kept []string
		for _, r := range registration {
			if safeSignupRole(r) {
				kept = append(kept, r)
			}
		}
		registration = kept
		if len(registration) == 0 {
			if safe := firstSafeRole(roles); safe != "" {
				registration = []string{safe}
			} else {
				self, mode, signUp = false, RegistrationAdmin, ""
			}
		}
	}

	identity := cleanList(req.IdentityFields)
	if len(identity) == 0 {
		identity = cleanList(policy.SignInFields)
	}
	if len(identity) == 0 {
		identity = []string{"email", "password"}
	}
	registrationFields := cleanList(req.RegistrationFields)
	if len(registrationFields) == 0 {
		registrationFields = cleanList(policy.RegistrationFields)
	}
	if len(registrationFields) == 0 {
		registrationFields = identity
	}

	return AuthContract{
		Enabled: true, SignIn: true, SignInRoute: signIn, SignOutRequired: true,
		IdentityFields: identity, RegistrationFields: registrationFields,
		PasswordResetRequired: req.PasswordResetRequired,
		RegistrationMode:      mode, SelfRegistration: self, SignUpRoute: signUp,
		RegistrationRoles: orEmptyList(registration),
		Provisioning: &Provisioning{
			Mode: mode, Role: clean(firstNonEmpty(req.ProvisioningRole, policy.ProvisioningRole)),
			AccountManagementRoute:    clean(firstNonEmpty(req.AccountManagementRoute, policy.AccountManagementRoute)),
			InvitationManagementRoute: clean(firstNonEmpty(req.InvitationManagementRoute, policy.InvitationManagementRoute)),
			InvitationAcceptRoute:     clean(firstNonEmpty(req.InvitationAcceptRoute, policy.InvitationAcceptRoute)),
			RequestAccessRoute:        clean(firstNonEmpty(req.RequestAccessRoute, policy.RequestAccessRoute)),
			AccessReviewRoute:         clean(firstNonEmpty(req.AccessReviewRoute, policy.AccessReviewRoute)),
		},
		AuthTransport:            clean(firstNonEmpty(req.AuthTransport, "provider_managed")),
		RoleSource:               "server-side session user record",
		SignedOutProtectedAccess: "redirect_to_sign_in",
		WrongRoleAccess:          "forbidden",
		RoleHome:                 roleHomes(roles, pages),
		Roles:                    roleRules(access, pages, roles),
		Rules: []string{
			"Never accept a role from a self-registration form unless the SRS explicitly allows that role.",
			"Check session and role on the server before protected reads or writes.",
			"Hiding navigation is not authorization.",
		},
	}
}

func matchRoute(pages []HandoffPage, pattern *regexp.Regexp) string {
	for _, page := range pages {
		if pattern.MatchString(page.Name + " " + page.Route) {
			return page.Route
		}
	}
	return ""
}

func firstSafeRole(roles []HandoffRole) string {
	for _, role := range roles {
		name := firstNonEmpty(role.Key, role.Name)
		if name != "" && safeSignupRole(name) {
			return name
		}
	}
	return ""
}

// roleHomes is where each role lands after signing in: the first protected
// page they may open.
func roleHomes(roles []HandoffRole, pages []HandoffPage) map[string]string {
	out := map[string]string{}
	for _, role := range roles {
		key := clean(firstNonEmpty(role.Key, role.Name))
		if key == "" {
			continue
		}
		for _, page := range pages {
			if page.Public {
				continue
			}
			allowed := map[string]bool{}
			for _, r := range page.AllowedRoles {
				allowed[snakeName(r)] = true
			}
			if len(allowed) == 0 || allowed[snakeName(key)] {
				out[key] = firstNonEmpty(page.Route, "/")
				break
			}
		}
	}
	return out
}

func roleRules(access []RoleAccess, pages []HandoffPage, _ []HandoffRole) []RoleRule {
	byName := map[string]HandoffPage{}
	for _, p := range pages {
		byName[strings.ToLower(p.Name)] = p
	}
	homes := roleHomes(nil, pages)
	_ = homes

	out := []RoleRule{}
	for _, row := range access {
		role := clean(row.Role)
		if role == "" {
			continue
		}
		var routes []string
		for _, name := range row.AllowedPages {
			if page, ok := byName[strings.ToLower(name)]; ok && !containsString(routes, page.Route) {
				routes = append(routes, page.Route)
			}
		}
		for _, page := range pages {
			if page.Public {
				continue
			}
			for _, r := range page.AllowedRoles {
				if snakeName(r) == snakeName(role) && !containsString(routes, page.Route) {
					routes = append(routes, page.Route)
				}
			}
		}
		var denied []string
		for _, page := range pages {
			if !page.Public && !containsString(routes, page.Route) {
				denied = append(denied, page.Route)
			}
		}
		out = append(out, RoleRule{
			Role: role, AllowedRoutes: orEmptyList(routes), DeniedRoutes: orEmptyList(denied),
			AllowedFunctions:    orEmptyList(row.AllowedFunctions),
			RestrictedFunctions: orEmptyList(row.RestrictedFunctions),
		})
	}
	// Home routes are per role name, and the access rows carry role keys.
	for i := range out {
		for role, home := range roleHomes([]HandoffRole{{Key: out[i].Role, Name: out[i].Role}}, pages) {
			if role == out[i].Role {
				out[i].HomeRoute = home
			}
		}
	}
	return out
}

// --- what each capability has to prove --------------------------------------------------

// FeatureContract is one capability with everything needed to build it and
// everything needed to prove it: its pages, its routes, its endpoints, its
// data and the journey it belongs to.
type FeatureContract struct {
	ID            string   `json:"id"`
	RequirementID string   `json:"requirement_id"`
	Requirement   string   `json:"requirement"`
	Kind          string   `json:"kind"`
	Module        string   `json:"module"`
	Roles         []string `json:"roles"`
	Pages         []string `json:"pages"`
	Routes        []string `json:"routes"`
	APIs          []string `json:"apis"`
	Data          []string `json:"data"`
	Workflows     []string `json:"workflows"`
	Acceptance    string   `json:"acceptance"`
	E2ERequired   bool     `json:"e2e_required"`
}

func buildFeatureContracts(requirements []HandoffRequirement, pages []HandoffPage,
	apis []HandoffAPI, workflows []HandoffWorkflow, acceptance []Acceptance) []FeatureContract {

	byKey := map[string]HandoffPage{}
	for _, page := range pages {
		for _, key := range []string{page.Name, page.Route, page.SRSRoute} {
			if key != "" {
				byKey[strings.ToLower(key)] = page
			}
		}
	}
	criterion := map[string]string{}
	for _, row := range acceptance {
		if id := clean(row.ID); id != "" {
			criterion[id] = clean(row.Criterion)
		}
	}

	out := []FeatureContract{}
	for _, req := range requirements {
		rid := clean(firstNonEmpty(req.SourceID, req.ID))
		proof := TraceRow{}
		if req.Proof != nil {
			proof = *req.Proof
		}

		var targetPages, routes []string
		roles := cleanList(req.Roles)
		for _, raw := range proof.Pages {
			page, ok := byKey[strings.ToLower(raw)]
			if !ok {
				continue
			}
			name := firstNonEmpty(page.Name, raw)
			if !containsString(targetPages, name) {
				targetPages = append(targetPages, name)
			}
			if page.Route != "" && !containsString(routes, page.Route) {
				routes = append(routes, page.Route)
			}
			for _, role := range page.AllowedRoles {
				if !containsString(roles, role) {
					roles = append(roles, role)
				}
			}
		}

		var related []string
		for _, flow := range workflows {
			shared := false
			for _, route := range flow.Routes {
				if containsString(routes, route) {
					shared = true
					break
				}
			}
			if !shared {
				continue
			}
			if flow.Name != "" {
				related = append(related, flow.Name)
			}
			if flow.Who != "" && !containsString(roles, flow.Who) {
				roles = append(roles, flow.Who)
			}
		}

		out = append(out, FeatureContract{
			ID: "CAP-" + firstNonEmpty(rid, itoa(len(out)+1)), RequirementID: rid,
			Requirement: clean(req.Text), Kind: firstNonEmpty(req.Kind, "feature"),
			Module: req.Module, Roles: orEmptyList(roles), Pages: orEmptyList(targetPages),
			Routes: orEmptyList(routes), APIs: matchingAPIs(req, proof, apis),
			Data: orEmptyList(proof.Tables), Workflows: orEmptyList(related),
			Acceptance:  firstNonEmpty(criterion[rid], proof.TestCase),
			E2ERequired: len(routes) > 0,
		})
	}
	return out
}

// matchingAPIs is which endpoints this requirement is about — by the data it
// touches first, then by a distinctive word it shares with the description.
func matchingAPIs(req HandoffRequirement, proof TraceRow, apis []HandoffAPI) []string {
	var words []string
	for _, table := range proof.Tables {
		words = append(words, strings.ToLower(strings.ReplaceAll(table, "_", " ")))
	}
	low := strings.ToLower(clean(req.Text))

	out := []string{}
	for _, ep := range apis {
		desc := strings.ToLower(clean(ep.Description))
		path := strings.ToLower(ep.Path)
		matched := false
		for _, word := range words {
			if word != "" && (strings.Contains(desc, word) ||
				strings.Contains(path, strings.TrimSuffix(word, "s"))) {
				matched = true
				break
			}
		}
		if !matched && low != "" && desc != "" {
			for _, token := range strings.Fields(low) {
				if len(token) > 5 && strings.Contains(desc, token) {
					matched = true
					break
				}
			}
		}
		if matched {
			out = append(out, ep.Method+" "+ep.Path)
		}
	}
	return out
}

// --- the contracts as prose ---------------------------------------------------------

// authPromptLines states the account rules in the order the builder needs
// them: sign in, then who may get an account, then what each role may reach.
func authPromptLines(contract AuthContract, auth bool) []string {
	if !contract.Enabled {
		_ = auth
		return []string{"AUTH CONTRACT:", "- No accounts. No login, sign-up or protected routes.", ""}
	}

	identity := contract.IdentityFields
	if len(identity) == 0 {
		identity = []string{"email", "password"}
	}
	lines := []string{
		"AUTH CONTRACT — IMPLEMENT BEFORE ROLE FEATURES:",
		"- Auth transport is provider-managed. Use the project's approved auth stack and its existing session/server APIs; do not build a parallel auth system or invent generic auth endpoints.",
		"- Sign in: " + orText(contract.SignInRoute, "required page from the SRS") +
			" using " + strings.Join(identity, ", "),
	}

	if contract.SelfRegistration {
		roles := orText(strings.Join(contract.RegistrationRoles, ", "),
			"the explicitly approved default role")
		lines = append(lines, "- Sign up: "+orText(contract.SignUpRoute, "required")+
			"; new accounts may become only: "+roles+".")
		if fields := strings.Join(contract.RegistrationFields, ", "); fields != "" {
			lines = append(lines, "- Registration fields: "+fields+
				". The role is assigned by the server, never chosen by the visitor.")
		}
	} else {
		lines = append(lines, "- No public sign-up. Do not invent a register page or role picker.")
		p := contract.Provisioning
		if p == nil {
			p = &Provisioning{}
		}
		role := orText(p.Role, "the approved provisioning role")
		switch p.Mode {
		case RegistrationAdmin:
			lines = append(lines, "- Account provisioning: "+role+" creates accounts at "+
				orText(p.AccountManagementRoute, "/admin/users")+".")
		case RegistrationInvite:
			lines = append(lines, "- Account provisioning: "+role+" sends invitations at "+
				orText(p.InvitationManagementRoute, "/admin/invitations")+
				"; recipients finish at "+orText(p.InvitationAcceptRoute, "/accept-invite")+".")
		case RegistrationRequest:
			lines = append(lines, "- Account provisioning: visitors request access at "+
				orText(p.RequestAccessRoute, "/request-access")+"; "+role+
				" reviews requests at "+orText(p.AccessReviewRoute, "/admin/access-requests")+".")
		}
	}

	lines = append(lines,
		"- Signed-out access to a protected route redirects to sign in; a signed-in user with the wrong role is refused.",
		"- The session user record is the role source. Never trust role values from forms, query strings or writable cookies.")

	for _, row := range contract.Roles {
		line := "- " + row.Role + ": allowed routes = " +
			orText(strings.Join(row.AllowedRoutes, ", "), "no protected route")
		if deny := strings.Join(row.DeniedRoutes, ", "); deny != "" {
			line += "; denied = " + deny
		}
		lines = append(lines, line)
		if len(row.AllowedFunctions) > 0 {
			lines = append(lines, "    can: "+strings.Join(row.AllowedFunctions, "; "))
		}
		if len(row.RestrictedFunctions) > 0 {
			lines = append(lines, "    cannot: "+strings.Join(row.RestrictedFunctions, "; "))
		}
	}
	return append(lines, "")
}

// featurePromptLines is the ledger: one row per capability, each with where it
// lives and what proves it.
func featurePromptLines(contracts []FeatureContract) []string {
	if len(contracts) == 0 {
		return nil
	}
	lines := []string{"FEATURE LEDGER — EACH ROW MUST BECOME WORKING CODE:"}
	for _, cap := range contracts {
		var target []string
		if len(cap.Roles) > 0 {
			target = append(target, "roles="+strings.Join(cap.Roles, ","))
		}
		if len(cap.Routes) > 0 {
			target = append(target, "routes="+strings.Join(cap.Routes, ","))
		}
		if len(cap.APIs) > 0 {
			target = append(target, "apis="+strings.Join(cap.APIs, ","))
		}
		if len(cap.Data) > 0 {
			target = append(target, "data="+strings.Join(cap.Data, ","))
		}
		if len(cap.Workflows) > 0 {
			target = append(target, "journey="+strings.Join(cap.Workflows, ","))
		}
		suffix := ""
		if len(target) > 0 {
			suffix = " | " + strings.Join(target, "; ")
		}
		lines = append(lines, "- "+cap.ID+": "+cap.Requirement+suffix)
		if cap.Acceptance != "" {
			lines = append(lines, "    proof: "+cap.Acceptance)
		}
	}
	return append(lines,
		"- Do not declare a capability complete because its page exists. Its action, persistence, access rule and next visible state must work.",
		"")
}
