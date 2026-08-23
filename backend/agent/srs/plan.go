package srs

import (
	"fmt"
	"regexp"
	"strings"
)

// The plan is what the customer approves, and it is a ceiling: the SRS's
// tables come from its records, its roles from its users, its pages from its
// screens. A record left out here cannot be added later. So the plan is built
// deterministically from the interview first, and the model only ever rewrites
// that skeleton — it never starts from a blank page.

// --- the skeleton ----------------------------------------------------------------

// pagePurpose says, in the customer's language, what each kind of page is for.
var pagePurpose = map[string]string{
	"dashboard":   "The first thing staff see, with the day's figures",
	"entity_crud": "Where these records are listed, added and edited",
	"report":      "Where the numbers are pulled together for printing or export",
	"settings":    "Where the app is configured",
	"marketing":   "The public page a visitor lands on",
	"auth":        "Where people sign in",
	"catalog":     "A browsable list",
	"detail":      "The full view of one item",
	"custom":      "The main working screen",
}

// anonymousRoles are the roles that are not really accounts.
var anonymousRoles = map[string]bool{
	"visitor": true, "guest": true, "public": true,
	"anonymous": true, "everyone": true,
}

// BuildOfflinePlan is a complete plan from the interview answers alone. It is
// what the customer gets when no model is reachable, and the starting point
// the model is asked to improve when one is.
func BuildOfflinePlan(project *Project, session *Session, brief string) *Plan {
	if project == nil {
		project = &Project{}
	}
	pack := session.Pack
	if pack == nil {
		appType := session.AppType
		if appType == "" {
			appType = DefaultAppType
		}
		pack = BuildPack(appType, firstNonEmpty(brief, project.RawIdea))
	}

	appName := firstText(answerOf(session, "app_name"), project.Title, "The app")

	label := pack.AppLabel
	if label == "" {
		label = "web application"
	}
	opening := appName + " is a " + label
	if pack.DomainLabel != "" && pack.DomainConfidence >= DomainFloor {
		opening += " for " + strings.ToLower(pack.DomainLabel)
	}
	outcome := firstText(answerOf(session, "core_outcome"))
	intent := strings.TrimSpace(opening + ". " + strings.TrimSpace(project.RawIdea))
	if outcome != "" {
		intent += " Primary success outcome: " + outcome
	}

	users := planUsers(session)
	screens := planScreens(session, pack, users)
	records := planRecords(session, pack)
	workflows := planJourneys(pack, outcome)
	features := planFeatures(session, pack)

	theme := firstText(answerOf(session, "theme_type"), "light")
	palette := firstText(answerOf(session, "color_palette"), pack.PaletteName, "indigo")
	devices := asList(answerOf(session, "responsive_pwa"))
	if len(devices) == 0 {
		devices = []string{"responsive"}
	}
	look := fmt.Sprintf("A %s theme on a %s palette, built for %s.",
		theme, strings.ReplaceAll(palette, "_", " "), strings.Join(devices, ", "))

	var assumptions []string
	if pack.AuthPolicy == "required" && len(rolesList(session)) == 0 {
		assumptions = append(assumptions, "People will need to log in, with a single admin role.")
	}
	if pack.AuthPolicy == "disabled" {
		assumptions = append(assumptions, "There is no login — everything on the site is public.")
	}
	if len(records) == 0 {
		assumptions = append(assumptions, "Nothing is stored between visits beyond enquiries.")
	}

	notes := CustomerNotes(session)
	for _, item := range SplitNotes(notes) {
		if !containsString(features, item) {
			features = append(features, item)
		}
	}

	plan := &Plan{
		AppName: appName, ProductIntent: intent,
		Users: users, Screens: screens, Records: records,
		Workflows: workflows, Features: features,
		AccountPolicy: accountPolicyFor(session, pack, users),
		LookAndFeel:   look, Assumptions: assumptions,
		OpenQuestions: []OpenQuestion{}, CustomerNotes: notes,
	}
	plan.normalise()
	return plan
}

func planUsers(session *Session) []PlanUser {
	var users []PlanUser
	for _, role := range rolesList(session) {
		users = append(users, PlanUser{
			Role:  sentenceCase(role),
			CanDo: asList(answerOf(session, "role_functions:"+role)),
		})
	}
	if len(users) == 0 {
		canDo := asList(answerOf(session, "page_functions"))
		if len(canDo) == 0 {
			canDo = []string{"Browse the site", "Get in touch"}
		}
		users = append(users, PlanUser{Role: "Visitor", CanDo: canDo})
	}
	return users
}

func planScreens(session *Session, pack *Pack, users []PlanUser) []Screen {
	var screens []Screen
	for _, page := range pack.Pages {
		purpose, ok := pagePurpose[page.Type]
		if !ok {
			purpose = "A screen in the app"
		}
		route := page.Route
		if route == "" {
			route = "/"
		}
		name := page.Name
		if name == "" {
			name = "Page"
		}
		screens = append(screens, Screen{
			Name: name, Route: route,
			Purpose: purpose + " (" + route + ").",
			Who:     screenAudience(page, users, pack),
		})
	}
	for _, section := range asList(answerOf(session, "sections")) {
		screens = append(screens, Screen{
			Name: sentenceCase(section), Route: "/",
			Purpose: "A section of the single page.", Who: []string{"Visitor"},
		})
	}
	return screens
}

func planRecords(session *Session, pack *Pack) []PlanRecord {
	var records []PlanRecord
	for _, table := range tablesList(session) {
		records = append(records, PlanRecord{
			Name:  sentenceCase(table),
			Keeps: titleEach(asList(answerOf(session, "table_entities:"+table))),
		})
	}
	for _, group := range []string{"lead_fields", "tool_inputs"} {
		fields := asList(answerOf(session, group))
		if len(fields) == 0 {
			continue
		}
		name := "Entry"
		if group == "lead_fields" {
			name = "Enquiry"
		}
		records = append(records, PlanRecord{Name: name, Keeps: titleEach(fields)})
	}

	// A record nobody described gets the fields this trade normally keeps.
	byName := map[string]Table{}
	for _, t := range pack.DomainTables {
		byName[strings.ToLower(t.TableName)] = t
	}
	for i := range records {
		if len(records[i].Keeps) > 0 {
			continue
		}
		known, ok := byName[strings.ReplaceAll(strings.ToLower(records[i].Name), " ", "_")]
		if !ok {
			continue
		}
		var keeps []string
		for _, f := range known.Fields {
			switch f.Name {
			case "", "id", "created_at", "updated_at":
				continue
			}
			keeps = append(keeps, sentenceCase(f.Name))
		}
		records[i].Keeps = keeps
	}
	return records
}

func planJourneys(pack *Pack, outcome string) []Journey {
	var flows []Journey
	if outcome != "" {
		flows = append(flows, Journey{Name: "Primary success path", Steps: []string{outcome}})
	}
	for _, w := range pack.DomainWorkflows {
		name := w.WorkflowName
		if name == "" {
			name = "Workflow"
		}
		flows = append(flows, Journey{Name: name, Steps: w.Steps})
	}
	return flows
}

// featureTopics are the answers that name something the app must do.
var featureTopics = map[string]bool{
	"role_functions": true, "page_functions": true, "offerings": true,
	"pos_payments": true, "pos_receipt": true, "pos_stock_rules": true,
	"saas_plans": true, "store_payments": true, "store_fulfilment": true,
	"dash_metrics": true, "dash_exports": true, "blog_workflow": true,
	"blog_organisation": true, "blog_engagement": true, "tool_outputs": true,
	"contact_channels": true, "social_proof": true,
}

func planFeatures(session *Session, pack *Pack) []string {
	features := []string{}
	for _, key := range answerKeys(session) {
		topic := key
		if i := strings.Index(key, ":"); i >= 0 {
			topic = key[:i]
		}
		if !featureTopics[topic] {
			continue
		}
		for _, item := range asList(session.Answers[key].Value) {
			if label := sentenceCase(item); !containsString(features, label) {
				features = append(features, label)
			}
		}
	}
	for _, feat := range pack.Features {
		if !containsString(features, feat) {
			features = append(features, feat)
		}
	}
	return features
}

// --- who may open a screen -------------------------------------------------------

// screenAudience reaches for evidence before it falls back: the trade's own
// page list first, then the kind of page, then the words in its name against
// what each role said they do. Every signed-in role is the last resort, not
// the first, because "everyone can see everything" is not an access boundary.
func screenAudience(page PackPage, users []PlanUser, pack *Pack) []string {
	selected := map[string]string{}
	for _, u := range users {
		selected[snakeValue(u.Role)] = u.Role
	}

	for _, src := range pack.DomainPublicPages {
		if page.Route == src.Route || strings.EqualFold(page.Name, src.PageName) {
			return []string{"Visitor"}
		}
	}
	for _, src := range pack.DomainProtectedPages {
		if page.Route != src.Route && !strings.EqualFold(page.Name, src.PageName) {
			continue
		}
		var roles []string
		for _, r := range src.AllowedRoles {
			if role, ok := selected[snakeValue(r)]; ok {
				roles = append(roles, role)
			}
		}
		if len(roles) > 0 {
			return roles
		}
	}

	switch page.Type {
	case "marketing", "catalog", "detail", "auth":
		return []string{"Visitor"}
	}

	// The pack's pages carry no purpose, so the name is all there is to match.
	words := map[string]bool{}
	for _, w := range longWord.FindAllString(strings.ToLower(page.Name), -1) {
		words[w] = true
	}
	var matched []string
	for _, u := range users {
		duties := strings.ToLower(strings.Join(u.CanDo, " "))
		for w := range words {
			if strings.Contains(duties, w) {
				matched = append(matched, u.Role)
				break
			}
		}
	}
	if len(matched) > 0 {
		return matched
	}

	var signedIn []string
	for _, u := range users {
		if !anonymousRoles[snakeValue(u.Role)] {
			signedIn = append(signedIn, u.Role)
		}
	}
	if len(signedIn) > 0 {
		return signedIn
	}
	return []string{"Visitor"}
}

var longWord = regexp.MustCompile(`[a-z]{4,}`)

// --- accounts ---------------------------------------------------------------------

var signInPage = regexp.MustCompile(`(?i)login|sign[ -]?in`)
var signUpPage = regexp.MustCompile(`(?i)register|sign[ -]?up`)

// identityContract splits what someone signs in with from what registration
// also asks for. A name identifies a person but cannot authenticate one.
func identityContract(session *Session) (signIn, register []string) {
	raw := asList(answerOf(session, "auth_identity"))
	if len(raw) == 0 {
		return []string{"email", "password"}, []string{"full_name", "email", "password"}
	}
	for _, field := range raw {
		key := snakeValue(field)
		if key == "full_name" || key == "name" {
			if !containsString(register, "full_name") {
				register = append(register, "full_name")
			}
			continue
		}
		if key == "phone_number" || key == "mobile" || key == "mobile_number" {
			key = "phone"
		}
		if key == "" {
			continue
		}
		if !containsString(signIn, key) {
			signIn = append(signIn, key)
		}
		if !containsString(register, key) {
			register = append(register, key)
		}
	}
	if !containsString(signIn, "password") {
		signIn = append(signIn, "password")
	}
	if !containsString(register, "password") {
		register = append(register, "password")
	}
	return signIn, register
}

func safeSignupRole(role string) bool {
	key := snakeValue(role)
	if key == "" {
		return false
	}
	for _, word := range privilegedSignup {
		if strings.Contains(key, word) {
			return false
		}
	}
	return true
}

// accountPolicyFor decides who may hold an account and how they get one.
func accountPolicyFor(session *Session, pack *Pack, users []PlanUser) *AccountPolicy {
	real := false
	for _, u := range users {
		if !anonymousRoles[snakeValue(u.Role)] {
			real = true
			break
		}
	}
	if pack.AuthPolicy == "disabled" || !real {
		return &AccountPolicy{AccountsRequired: false, RegistrationMode: RegistrationNone}
	}

	signIn, register := identityContract(session)
	mode := firstText(firstAnswer(session, "account_creation", "saas_signup"))
	if mode == "" {
		mode = RegistrationAdmin
		for _, p := range pack.Pages {
			if signUpPage.MatchString(p.Name + " " + p.Route) {
				mode = RegistrationOpen
				break
			}
		}
	}

	role := firstText(answerOf(session, "signup_role"))
	if mode == RegistrationOpen {
		if role != "" && !safeSignupRole(role) {
			role = ""
		}
		if role == "" {
			role = firstUnprivilegedRole(users)
		}
		if role == "" {
			mode = RegistrationAdmin
		}
	}

	policy := &AccountPolicy{
		AccountsRequired: true,
		SignInFields:     signIn, RegistrationFields: register,
		RegistrationMode: mode, SignInRoute: "/login",
	}
	if role != "" {
		policy.RegistrationRole = sentenceCase(role)
	}
	for _, p := range pack.Pages {
		if signInPage.MatchString(p.Name) {
			policy.SignInRoute = p.Route
			break
		}
	}
	if mode == RegistrationOpen {
		policy.SignUpRoute = "/register"
		for _, p := range pack.Pages {
			if signUpPage.MatchString(p.Name) {
				policy.SignUpRoute = p.Route
				break
			}
		}
	}

	switch mode {
	case RegistrationAdmin:
		policy.ProvisioningRole = sentenceCase(provisioningRole(users))
		policy.AccountManagementRoute = "/admin/users"
	case RegistrationInvite:
		policy.ProvisioningRole = sentenceCase(provisioningRole(users))
		policy.InvitationManagementRoute = "/admin/invitations"
		policy.InvitationAcceptRoute = "/accept-invite"
	case RegistrationRequest:
		policy.ProvisioningRole = sentenceCase(provisioningRole(users))
		policy.RequestAccessRoute = "/request-access"
		policy.AccessReviewRoute = "/admin/access-requests"
	}
	return policy
}

// firstUnprivilegedRole is the role public sign-up may hand out when nobody
// named one. A moderator is refused a chosen role but accepted as a fallback,
// which is how the interview's own two lists differ.
func firstUnprivilegedRole(users []PlanUser) string {
	for _, u := range users {
		key := snakeValue(u.Role)
		if key == "" {
			continue
		}
		privileged := false
		for _, word := range privilegedSignup[:len(privilegedSignup)-1] {
			if strings.Contains(key, word) {
				privileged = true
				break
			}
		}
		if !privileged {
			return u.Role
		}
	}
	return ""
}

// provisioningRole is whoever creates the accounts: a privileged role if there
// is one, otherwise anyone who is not a visitor.
func provisioningRole(users []PlanUser) string {
	for _, u := range users {
		key := snakeValue(u.Role)
		for _, word := range privilegedSignup {
			if strings.Contains(key, word) {
				return u.Role
			}
		}
	}
	for _, u := range users {
		switch snakeValue(u.Role) {
		case "visitor", "guest", "public":
			continue
		}
		return u.Role
	}
	return ""
}

// --- the customer's own words ------------------------------------------------------

// CustomerNotes is whatever they typed in the free box at the end.
func CustomerNotes(session *Session) string {
	if session == nil {
		return ""
	}
	entry, ok := session.Answers["extra_notes"]
	if !ok {
		return ""
	}
	if text := strings.TrimSpace(strings.Join(asList(entry.Value), " ")); text != "" {
		return text
	}
	return strings.TrimSpace(entry.Text)
}

var sentenceSplit = regexp.MustCompile(`[.!?;]\s+|\n+`)
var clauseSplit = regexp.MustCompile(`,\s*(?:and\s+)?|\s+and\s+`)

// SplitNotes reads one free-text paragraph as separate things to build, so
// nothing they mentioned in passing is summarised away.
func SplitNotes(notes string) []string {
	if strings.TrimSpace(notes) == "" {
		return nil
	}
	var out []string
	for _, sentence := range sentenceSplit.Split(notes, -1) {
		for _, part := range clauseSplit.Split(sentence, -1) {
			item := sentenceCase(strings.Trim(part, " .;,"))
			if len(item) > 3 && !containsString(out, item) {
				out = append(out, item)
			}
		}
	}
	return out
}

// --- small shared helpers --------------------------------------------------------------

// answerKeys lists the answers in the order they were given, so a plan built
// twice from the same session comes out the same way round.
func answerKeys(session *Session) []string {
	if session == nil || len(session.Answers) == 0 {
		return nil
	}
	out := make([]string, 0, len(session.Answers))
	seen := map[string]bool{}
	for _, key := range session.Asked {
		if _, ok := session.Answers[key]; ok && !seen[key] {
			seen[key] = true
			out = append(out, key)
		}
	}
	for _, key := range keysSorted(session.Answers) {
		if !seen[key] {
			out = append(out, key)
		}
	}
	return out
}

// asList reads an answer as a list of non-empty strings, whatever shape it was
// stored in.
func asList(v any) []string {
	var out []string
	for _, item := range stringList(v) {
		if item = strings.TrimSpace(item); item != "" {
			out = append(out, item)
		}
	}
	return out
}

func titleEach(values []string) []string {
	out := make([]string, 0, len(values))
	for _, v := range values {
		out = append(out, sentenceCase(v))
	}
	return out
}

// sentenceCase is the plan's own capitalisation: underscores become spaces and
// only the first letter is raised, so "full_name" reads "Full name" rather
// than the shouty "Full Name" a role label wants.
func sentenceCase(text string) string {
	text = strings.TrimSpace(strings.ReplaceAll(text, "_", " "))
	if text == "" {
		return ""
	}
	return strings.ToUpper(text[:1]) + text[1:]
}

// firstText is the first of these that reads as a non-empty string.
func firstText(values ...any) string {
	for _, v := range values {
		if v == nil {
			continue
		}
		if text := strings.TrimSpace(fmt.Sprint(v)); text != "" && text != "<nil>" {
			return text
		}
	}
	return ""
}
