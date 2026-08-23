package srs

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// This is the model's half of the plan: what it is told, what it must hand
// back, and what happens when it cannot. The plan itself — the skeleton, the
// merge, the markdown, the approval gate — is in plan.go, and the rule that
// keeps the two apart is that nothing here may be the only source of anything:
// every failure below falls back to the deterministic plan.

const planSystem = `You are a product architect writing a plan for a NON-TECHNICAL customer to approve. You are given everything one customer asked for, and you produce one complete plan of the application that answers it.

Model what people are trying to DO before you decide what screens exist. A workflow is a whole job, start to finish: who starts it, what they see, what they change, and where they end up. Screens are then whatever those workflows need — nothing more.

This matters because the usual failure is to reach for the shape of the category. A shop gets a storefront, a cart and an admin table; a business tool gets a dashboard, a list and a form. Those are not requirements, they are habits, and an app built from them fits nobody.

This plan is a CEILING, not a summary. Everything written afterwards is derived from it and nothing may exceed it: the specification's tables come from your ` + "`records`" + ` and nothing else, its roles from your ` + "`users`" + `, its pages from your ` + "`screens`" + `, and every one of its requirements from your ` + "`users`" + `, ` + "`features`" + ` and ` + "`workflows`" + `. A record you leave out cannot be added later; a screen you forget is a screen the finished app does not have. Say the whole thing here.

Rules:
- Every screen carries a ` + "`purpose`" + ` naming the job it exists for. If the only honest purpose is "applications like this usually have one", do not create it.
- Every record lists at least two things it ` + "`keeps`" + `. A record with no fields becomes an empty table. Write plain labels — "Price", not "Price (money)" — because these become field names literally.
- ` + "`workflows`" + ` are the USER JOURNEYS, and there is one for EVERY kind of person in ` + "`users`" + ` — not only the customer's. Each runs a whole job start to finish, in three steps or more, and names the screens it passes through: who starts it (` + "`who`" + ` is their role), what they see, what they change, where they end up. A shop's owner has a journey ("Restocking a title": Admin Books → edit stock → back to the list) exactly as its customer does ("Buying a book": Catalogue → Book Detail → Cart → Checkout → Order History). A role with no journey is a role whose half of the app nobody has thought through.
- Where people sign in, each of them gets at least two ` + "`can_do`" + ` lines.
- Ask an open question rather than inventing an answer. Mark it required when the app cannot be specified without it. Give every question 2-4 ` + "`options`" + `: the answers you would accept, each one short, concrete and a real alternative to the others. A question that offers "coupon codes" and "a field on the order" can be settled with one click; the same question with no options gets answered "yes", which settles nothing and asks it again next round. Leave ` + "`options`" + ` out only where no list could be right — a name, a number, a free description.
- Use only the records, roles and actions the answers support. Do not add a record because the domain usually has one. The customer's stated main success outcome is a hard requirement: represent it explicitly in ` + "`product_intent`" + ` and in at least one workflow or feature so it can become an acceptance proof.
- Plain language throughout. No jargon: say "records" not "entities", "log in" not "authentication", "pages" not "routes".
- If accounts exist, ` + "`account_policy`" + ` is mandatory. State exactly who may create an account, the ONE role public sign-up creates, and where sign-in/sign-up live. For admin-created, invite, or request-access modes, name the ` + "`provisioning_role`" + `. Never let a public form choose Admin/Manager/Staff.
- Screen ` + "`who`" + ` is an access boundary. Public screens use Visitor/Everyone only; protected screens list only the roles that may open them. Do not put every role on every screen.

Return ONLY a JSON object:
{
  "app_name": "what the thing is called — return it only if you are changing it",
  "product_intent": "one paragraph: what this is and who it is for",
  "users": [{"role": "Cashier", "can_do": ["short sentence", ...]}],
  "screens": [{"name": "Sale Terminal", "route": "/sale", "purpose": "why it exists", "who": ["Cashier"]}],
  "records": [{"name": "Product", "keeps": ["Name", "Price", ...]}],
  "workflows": [{"name": "Taking a sale", "who": "Cashier",
                 "steps": ["Cashier opens the Sale Terminal", "...", "..."]}],
  "features": ["short sentence describing ONE thing the software does", ...],
  "account_policy": {
    "accounts_required": true,
    "sign_in_fields": ["email", "password"],
    "registration_fields": ["full_name", "email", "password"],
    "registration_mode": "open | admin_created | invite | request | none",
    "registration_role": "Customer or null",
    "provisioning_role": "Admin or null",
    "sign_in_route": "/login or null",
    "sign_up_route": "/register or null"
  },
  "look_and_feel": "one or two sentences on theme, colour and devices",
  "assumptions": ["something we decided for them, stated plainly"],
  "open_questions": [{"question": "...", "required": true,
                      "options": ["one way it could go", "the other way", ...]}]
}`

const revisionTail = `Do exactly what they asked for, and nothing else. A revision is not a re-plan: do not rewrite wording you were not asked about, do not drop screens, records, workflows or features that are already in the plan above, and do not re-derive the plan from the answers — those are context for understanding the request, not the plan.
Return the complete revised plan. Any section you leave out of your JSON is kept exactly as it stands above, so it is safe to return only the sections you actually changed — but a section you DO return replaces that section outright, so return it in full, including the items you are keeping.
` + "`open_questions`" + ` is the exception you must always return. Drop every question their message answers — an answer that picks one of the options you offered settles that question completely — and keep the ones it does not touch. Return ` + "`[]`" + ` when nothing is left to ask; an empty list is a valid and expected answer, and it is what lets them approve the plan. Do NOT raise new questions on a revision: they came here to settle the plan, and a round that answers two questions and asks two more never ends.`

// --- the depth floor ---------------------------------------------------------------

// checkPlan is the floor a returned plan has to clear, sized to the kind of
// app this is. A failed check goes back to the model with its own words, which
// is what gets a small local model to a depth it missed the first time.
func checkPlan(plan *Plan, pack *Pack) error {
	if len(strings.TrimSpace(plan.ProductIntent)) < 120 {
		return fmt.Errorf("'product_intent' must be a full paragraph saying what this is " +
			"and who it is for — one line is not enough for someone to approve")
	}

	var screens []Screen
	for _, s := range plan.Screens {
		if strings.TrimSpace(s.Name) != "" {
			screens = append(screens, s)
		}
	}
	if len(screens) == 0 {
		return fmt.Errorf("provide at least one screen, each with a 'name' and a 'purpose'")
	}
	var blank []string
	for _, s := range screens {
		if strings.TrimSpace(s.Purpose) == "" {
			blank = append(blank, s.Name)
		}
	}
	if len(blank) > 0 {
		return fmt.Errorf("every screen needs a 'purpose' naming the job it exists for; "+
			"missing on: %s", listOf(blank))
	}
	if len(asList(plan.Features)) == 0 {
		return fmt.Errorf("provide the product capabilities the customer actually requested; " +
			"do not invent features to satisfy a count")
	}

	archetype := ""
	if pack != nil {
		archetype = pack.Archetype
	}
	switch archetype {
	case archetypeLanding, archetypeTool, "marketing":
		return nil
	}

	var records []PlanRecord
	for _, r := range plan.Records {
		if strings.TrimSpace(r.Name) != "" {
			records = append(records, r)
		}
	}
	if len(records) == 0 {
		return fmt.Errorf("this record-based app needs at least one named business record; " +
			"use only records supported by the customer's answers")
	}
	var empty, shallow []string
	for _, r := range records {
		switch len(asList(r.Keeps)) {
		case 0:
			empty = append(empty, r.Name)
		case 1:
			shallow = append(shallow, r.Name)
		}
	}
	if len(empty) > 0 {
		return fmt.Errorf("every record needs at least 2 things it 'keeps'; empty on: %s", listOf(empty))
	}
	if len(shallow) > 0 {
		return fmt.Errorf("these records keep only one thing, which is not a record: %s", listOf(shallow))
	}

	deep := 0
	for _, w := range plan.Workflows {
		if len(asList(w.Steps)) >= 2 {
			deep++
		}
	}
	if deep == 0 {
		return fmt.Errorf("provide at least one 'workflow' with 2 or more steps — " +
			"a whole job from start to finish")
	}

	if pack == nil || pack.AuthPolicy != "required" {
		return nil
	}
	return checkAccounts(plan)
}

// checkAccounts is the part of the floor that only applies where people sign
// in. Getting it wrong is a privilege escalation, not a wording problem.
func checkAccounts(plan *Plan) error {
	able := 0
	for _, u := range plan.Users {
		if len(asList(u.CanDo)) >= 2 {
			able++
		}
	}
	if able == 0 {
		return fmt.Errorf("this app has people who sign in, so at least one entry in " +
			"'users' needs 2 or more 'can_do' lines")
	}

	policy := plan.AccountPolicy
	if policy == nil || !policy.AccountsRequired {
		return fmt.Errorf("account_policy is required for an app with sign-in")
	}
	switch policy.RegistrationMode {
	case RegistrationOpen, RegistrationAdmin, RegistrationInvite, RegistrationRequest, RegistrationNone:
	default:
		return fmt.Errorf("account_policy.registration_mode must say how accounts are created")
	}
	if policy.RegistrationMode == RegistrationOpen {
		role := strings.TrimSpace(policy.RegistrationRole)
		if role == "" {
			return fmt.Errorf("public sign-up needs one explicit registration_role")
		}
		if !safeSignupRole(role) {
			return fmt.Errorf("public sign-up cannot create a privileged role")
		}
	}
	switch policy.RegistrationMode {
	case RegistrationAdmin, RegistrationInvite, RegistrationRequest:
		if strings.TrimSpace(policy.ProvisioningRole) == "" {
			return fmt.Errorf("%s account creation needs one explicit provisioning_role",
				policy.RegistrationMode)
		}
	}
	return nil
}

// listOf names the first five offenders, which is as many as anyone reads.
func listOf(names []string) string {
	if len(names) > 5 {
		names = names[:5]
	}
	return strings.Join(names, ", ")
}

// --- generating ---------------------------------------------------------------------

// GeneratePlan builds the enriched plan, falling back to its skeleton. It only
// returns an error when the customer asked for a language the model could not
// produce, because then there is no plan to show them at all — a plan in the
// wrong language is worse than none.
func (s *Service) GeneratePlan(ctx context.Context, project *Project, session *Session,
	brief string, previous *Plan, revision string, coverage map[string]any) (*Plan, error) {

	if project == nil {
		project = &Project{}
	}
	pid := project.ID
	language := project.Language
	skeleton := BuildOfflinePlan(project, session, brief)
	pack := session.Pack
	if pack == nil {
		pack = &Pack{}
	}

	prompt := planPrompt(project, session, pack, brief, previous, revision, coverage, skeleton)
	s.emit(ctx, pid, "PlanGeneratorAgent", "Turning the answers into a plan…", "info", 30, nil)

	var answer Plan
	err := s.LLM.JSONValid(ctx, roleSRS, planSystem, prompt, &answer, func() error {
		return checkPlan(&answer, pack)
	})
	if err == nil {
		base := skeleton
		if previous != nil {
			base = previous
		}
		merged := mergePlan(base, &answer)
		s.emit(ctx, pid, "PlanGeneratorAgent", fmt.Sprintf(
			"Plan ready: %d screens, %d record types, %d features.",
			len(merged.Screens), len(merged.Records), len(merged.Features)),
			"success", 90, nil)
		return merged, nil
	}

	if !IsEnglish(language) {
		message := fmt.Sprintf("The selected SRS model could not produce the plan in %s. "+
			"Check that the selected model is available and try again.", language)
		s.emit(ctx, pid, "PlanGeneratorAgent", message, "error", 90, nil)
		s.recordError(ctx, pid, "PlanGeneratorAgent", err)
		return nil, fmt.Errorf("%s", message)
	}
	s.warn(ctx, pid, "PlanGeneratorAgent", fmt.Sprintf(
		"LLM not used (%s) — using the plan built from your answers.", truncate(err.Error(), 140)), 90)

	// A revision that could not be applied says so in the plan rather than
	// silently handing back the plan they asked to change.
	if previous != nil && revision != "" {
		out := *previous
		out.Assumptions = append(append([]string{}, previous.Assumptions...),
			"Requested change not applied automatically: "+truncate(revision, 200))
		return &out, nil
	}
	return skeleton, nil
}

// planPrompt is everything the model is told, in the order it is told it.
func planPrompt(project *Project, session *Session, pack *Pack, brief string,
	previous *Plan, revision string, coverage map[string]any, skeleton *Plan) string {

	var parts []string
	add := func(blocks ...string) {
		for _, b := range blocks {
			if strings.TrimSpace(b) != "" {
				parts = append(parts, b, "")
			}
		}
	}

	context := customerContext(brief, session, project)
	if context == "" {
		context = "THE CUSTOMER'S IDEA:\n" + firstNonEmpty(brief, project.RawIdea)
	}
	parts = append(parts, context, "",
		fmt.Sprintf("APP TYPE: %s (%s)", firstNonEmpty(pack.AppLabel, "unknown"), pack.Archetype),
		fmt.Sprintf("BUSINESS DOMAIN: %s", firstNonEmpty(pack.DomainLabel, "general")))
	if rule := outputLanguageRule(project.Language, "approved plan"); rule != "" {
		parts = append(parts, rule)
	}
	parts = append(parts, "")

	add(whatWeWorkedOut(project), domainLibrary(pack))
	add("WHAT THEY ANSWERED:\n" + answersDigest(session))
	add(whatNobodyAsked(coverage))

	if notes := CustomerNotes(session); notes != "" {
		add("IN THE CUSTOMER'S OWN WORDS — they wrote this at the end, unprompted, " +
			"when asked what else the site should show:\n\"\"\"" + notes + "\"\"\"")
		add("Everything they named there must appear in the plan — as a screen, a " +
			"section, a record or a feature, whichever it actually is. Do not " +
			"summarise it away, and do not drop the parts you have no question for.")
	}

	if previous == nil {
		parts = append(parts,
			"A PLAN ASSEMBLED FROM THOSE ANSWERS (rewrite it in their terms; keep "+
				"anything that is already right):", "```json", jsonLine(skeleton), "```")
	} else {
		parts = append(parts,
			"## THE PLAN AS IT STANDS — this is the plan you are editing",
			"```json", jsonLine(previous), "```", "",
			"## WHAT THE CUSTOMER WANTS CHANGED — their words, in full",
			firstNonEmpty(revision, "(no change requested)"), "", revisionTail)
	}
	return strings.Join(parts, "\n")
}

func jsonLine(v any) string {
	raw, err := json.Marshal(v)
	if err != nil {
		return "{}"
	}
	return string(raw)
}

// outputLanguageRule tells the model which language to answer in, and is empty
// for English because saying so only wastes context.
func outputLanguageRule(language, artifact string) string {
	if IsEnglish(language) {
		return ""
	}
	return fmt.Sprintf("OUTPUT LANGUAGE: write every human-readable word of the %s in %s. "+
		"Leave routes, field names, identifiers and numbers exactly as they are.",
		artifact, language)
}

// --- the context blocks ----------------------------------------------------------------

// customerContext is the idea and everything they have typed since, in their
// own vocabulary — which is the one the plan has to answer in.
func customerContext(brief string, session *Session, project *Project) string {
	idea := brief
	if session != nil {
		idea = firstNonEmpty(idea, session.RawIdea)
	}
	if project != nil {
		idea = firstNonEmpty(idea, project.RawIdea)
	}
	idea = strings.TrimSpace(idea)

	var typed, attached []string
	seen := map[string]bool{}
	for _, key := range answerKeys(session) {
		entry := session.Answers[key]
		if text := strings.TrimSpace(entry.Text); text != "" && !seen[strings.ToLower(text)] {
			seen[strings.ToLower(text)] = true
			typed = append(typed, "- "+text)
		}
		for _, att := range entry.Attachments {
			if att = strings.TrimSpace(att); att != "" && !seen[strings.ToLower(att)] {
				seen[strings.ToLower(att)] = true
				attached = append(attached, "- "+att)
			}
		}
	}
	if idea == "" && len(typed) == 0 && len(attached) == 0 {
		return ""
	}

	parts := []string{"THE CUSTOMER'S OWN WORDS — this is their app, and their " +
		"vocabulary is the one to answer in:"}
	if idea != "" {
		parts = append(parts, "\"\"\"\n"+truncate(idea, 3000)+"\n\"\"\"")
	}
	if len(typed) > 0 {
		parts = append(parts, "What they have typed themselves since:\n"+
			truncate(strings.Join(typed, "\n"), 1500))
	}
	if len(attached) > 0 {
		parts = append(parts, "From files they attached to their answers (a photo, a PDF "+
			"or something they said out loud — treat it as them talking):\n"+
			truncate(strings.Join(attached, "\n"), 2500))
	}
	return strings.Join(parts, "\n\n")
}

// answersDigest is what they were asked and what they said back.
func answersDigest(session *Session) string {
	if session == nil {
		return "(nothing recorded)"
	}
	byKey := map[string]Question{}
	for _, q := range session.Questions {
		if q.ID != "" {
			byKey[q.ID] = q
		}
	}

	var lines []string
	for _, key := range answerKeys(session) {
		entry := session.Answers[key]
		value := renderAnswer(entry.Value)
		question := byKey[key].Question
		if question == "" {
			question = key
		}
		lines = append(lines, "- "+question+" → "+value)

		typed := strings.TrimSpace(entry.Text)
		if typed != "" && !strings.Contains(value, typed) {
			lines = append(lines, "    they typed: "+typed)
		}
		for _, att := range entry.Attachments {
			if att = strings.TrimSpace(att); att != "" {
				lines = append(lines, "    they attached: "+att)
			}
		}
		if why := strings.TrimSpace(byKey[key].WhyNeeded); why != "" && typed == "" {
			lines = append(lines, "    (asked because: "+why+")")
		}
	}
	if len(lines) == 0 {
		return "(nothing recorded)"
	}
	return strings.Join(lines, "\n")
}

func renderAnswer(value any) string {
	switch v := value.(type) {
	case nil:
		return ""
	case string:
		return v
	case []string:
		return strings.Join(v, ", ")
	case []any:
		parts := make([]string, 0, len(v))
		for _, item := range v {
			parts = append(parts, fmt.Sprint(item))
		}
		return strings.Join(parts, ", ")
	case map[string]any:
		return jsonLine(v)
	}
	return fmt.Sprint(value)
}

// whatNobodyAsked names the areas the interview never covered. Naming them is
// the point: an assumption nobody can see is one nobody can correct.
func whatNobodyAsked(coverage map[string]any) string {
	if coverage == nil {
		return ""
	}
	critical := stringList(coverage["critical_missing"])
	optional := stringList(coverage["optional_missing"])
	var other []string
	for _, area := range stringList(coverage["missing"]) {
		if !containsString(critical, area) && !containsString(optional, area) {
			other = append(other, area)
		}
	}
	if len(critical)+len(optional)+len(other) == 0 {
		return ""
	}

	parts := []string{"NOBODY ASKED ABOUT THESE — the interview never covered them:"}
	if len(critical) > 0 {
		parts = append(parts, "  important: "+strings.Join(critical, ", "))
	}
	if minor := append(optional, other...); len(minor) > 0 {
		parts = append(parts, "  minor: "+strings.Join(minor, ", "))
	}
	parts = append(parts, "For each one, either state plainly in `assumptions` what you "+
		"decided on their behalf, or raise it in `open_questions`. Do not leave it "+
		"silently settled — an assumption nobody can see is one nobody can correct.")
	return strings.Join(parts, "\n")
}

// whatWeWorkedOut is what the classifier already decided, which the planner
// would otherwise re-derive from scratch and get differently.
func whatWeWorkedOut(project *Project) string {
	if project == nil {
		return ""
	}
	cls := project.Classification
	var rows []string
	if domain := firstNonEmpty(project.DetectedDomain, firstText(cls["detected_domain"])); domain != "" {
		rows = append(rows, "  domain: "+domain)
	}
	if kind := firstText(cls["app_type"]); kind != "" {
		rows = append(rows, "  kind of app: "+kind)
	}
	if why := firstText(cls["reasoning"]); why != "" {
		rows = append(rows, "  why we think so: "+truncate(why, 300))
	}
	if overall := firstText(project.Complexity["overall"]); overall != "" {
		rows = append(rows, "  complexity: "+overall)
	}
	var stack []string
	for _, key := range keysSorted(project.SuggestedStack) {
		if key == "locked" {
			continue
		}
		if value := firstText(project.SuggestedStack[key]); value != "" {
			stack = append(stack, key+"="+value)
		}
	}
	if len(stack) > 0 {
		rows = append(rows, "  stack already fixed: "+strings.Join(stack, ", "))
	}
	if len(rows) == 0 {
		return ""
	}
	return "WHAT WE HAVE ALREADY WORKED OUT ABOUT THIS BUSINESS:\n" + strings.Join(rows, "\n")
}

// domainLibrary is what apps in this trade usually hold — offered as a menu,
// never as a checklist, because the rule against adding a record the answers
// do not support still holds.
func domainLibrary(pack *Pack) string {
	if pack == nil {
		return ""
	}
	thin := pack.Archetype == archetypeLanding || pack.Archetype == archetypeTool

	var rows []string
	if !thin && len(pack.DomainTables) > 0 {
		var shaped []string
		for _, t := range pack.DomainTables {
			if t.TableName == "" {
				continue
			}
			var fields []string
			for _, f := range t.Fields {
				if f.Name != "" {
					fields = append(fields, f.Name)
				}
			}
			if len(fields) == 0 {
				fields = []string{"—"}
			}
			shaped = append(shaped, "  "+t.TableName+": "+strings.Join(fields, ", "))
		}
		if len(shaped) > 0 {
			rows = append(rows, "Records this trade usually keeps:\n"+strings.Join(shaped, "\n"))
		}
	}

	offered := []struct {
		label string
		items []string
	}{{"Modules", pack.DomainModules}}
	if !thin {
		var integrations, operations []string
		for _, i := range pack.DomainIntegrations {
			integrations = append(integrations, i.Name)
		}
		for _, o := range pack.Operations {
			operations = append(operations, firstNonEmpty(o.Name, o.ID))
		}
		offered = []struct {
			label string
			items []string
		}{
			{"Roles", pack.Roles}, {"Modules", pack.DomainModules},
			{"Integrations", integrations}, {"Things it usually does", operations},
		}
	}
	for _, group := range offered {
		if flat := asList(group.items); len(flat) > 0 {
			rows = append(rows, group.label+": "+strings.Join(flat, ", "))
		}
	}

	if len(pack.RequiredPages) > 0 {
		rows = append(rows, "Pages this kind of app cannot do without: "+
			strings.Join(pack.RequiredPages, ", "))
	}
	if len(pack.ProhibitedPageTypes) > 0 {
		rows = append(rows, "Pages this kind of app must NOT have: "+
			strings.Join(pack.ProhibitedPageTypes, ", "))
	}
	if len(rows) == 0 {
		return ""
	}
	return "WHAT APPS IN THIS TRADE USUALLY HAVE — a menu, not a checklist. Use an " +
		"item only where the answers above support it; the rule against adding a " +
		"record because the domain usually has one still holds.\n" +
		strings.Join(rows, "\n\n")
}

// --- the English plan ------------------------------------------------------------------

// machineKeys are the values that are identifiers rather than prose. Nothing
// under one of them is ever translated.
var machineKeys = map[string]bool{
	"id": true, "key": true, "role_key": true, "route": true, "method": true,
	"path": true, "required": true, "priority": true, "type": true,
	"table_name": true, "field_name": true, "requirement_id": true,
	"app_name": true,
}

// contractToken matches the things a translation must carry over untouched:
// backticked code, requirement ids, and routes.
var contractToken = regexp.MustCompile("`[^`\n]+`|(?i)\\b(?:FR|NFR|AC|WF|CAP|TC)-[A-Za-z0-9_.:-]+\\b|/[A-Za-z0-9_./{}:-]+")

const translateSystem = `Translate each supplied approved software-plan text into clear English. Return ONLY JSON as {"translations":[{"index":0,"text":"..."}]}. Return every numeric index exactly once. Preserve exact meaning, scope, ordering, project/app names, IDs, routes, code identifiers, numbers and technical product names. Never add, remove, merge or split a requirement.`

type translation struct {
	Index int    `json:"index"`
	Text  string `json:"text"`
}

// englishPlanNode makes the English copy the SRS is written from. The plan the
// customer approved stays in their language; only the graph's copy changes.
//
// Unlike the Python, the model is never asked for the plan itself — only for a
// numbered list of translated strings, which are substituted back in here. A
// bad translation therefore cannot reshape the plan, so the shape check the
// Python needed afterwards has nothing left to catch.
func (s *Service) englishPlanNode(ctx context.Context, state any) (any, error) {
	st := state.(*State)
	if st.Plan == nil {
		return st, nil
	}
	language := firstNonEmpty(st.Language, "English")
	if st.Project != nil {
		language = firstNonEmpty(st.Project.Language, language)
	}
	if IsEnglish(language) {
		return st, nil
	}

	raw, err := json.Marshal(st.Plan)
	if err != nil {
		return st, err
	}
	var tree any
	if err := json.Unmarshal(raw, &tree); err != nil {
		return st, err
	}

	texts := collectText(tree, "", map[string]bool{}, nil)
	if len(texts) == 0 {
		return st, nil
	}

	s.emit(ctx, st.ProjectID, "EnglishPlanAgent",
		"Preparing the approved plan for the English SRS…", "info", 8, nil)

	payload := struct {
		Texts []translation `json:"texts"`
	}{}
	for i, text := range texts {
		payload.Texts = append(payload.Texts, translation{Index: i, Text: text})
	}
	var answer struct {
		Translations []translation `json:"translations"`
	}
	err = s.LLM.JSONValid(ctx, roleSRS, translateSystem, jsonLine(payload), &answer, func() error {
		return checkTranslations(texts, answer.Translations)
	})
	if err != nil {
		s.recordError(ctx, st.ProjectID, "EnglishPlanAgent", err)
		return st, fmt.Errorf("the selected SRS model could not prepare the approved plan " +
			"for the English SRS. Check that the selected model is available and try again")
	}

	byText := map[string]string{}
	for _, row := range answer.Translations {
		byText[texts[row.Index]] = strings.TrimSpace(row.Text)
	}
	var english Plan
	if err := json.Unmarshal([]byte(jsonLine(applyText(tree, "", byText))), &english); err != nil {
		return st, err
	}
	english.AppName = st.Plan.AppName

	st.Plan = &english
	st.PlanMarkdown = RenderPlanMarkdown(&english, english.AppName)
	s.emit(ctx, st.ProjectID, "EnglishPlanAgent",
		"English plan source ready; writing the SRS in English.", "success", 10, nil)
	return st, nil
}

// checkTranslations refuses a reply that is missing an index, repeats one, or
// drops a route or requirement id out of the text it was given.
func checkTranslations(texts []string, rows []translation) error {
	if len(rows) != len(texts) {
		return fmt.Errorf("return every translation exactly once")
	}
	seen := map[int]bool{}
	for _, row := range rows {
		if row.Index < 0 || row.Index >= len(texts) || seen[row.Index] || strings.TrimSpace(row.Text) == "" {
			return fmt.Errorf("translation indexes must be unique and complete")
		}
		seen[row.Index] = true
		for _, token := range contractToken.FindAllString(texts[row.Index], -1) {
			if !strings.Contains(row.Text, token) {
				return fmt.Errorf("translation changed protected contract tokens")
			}
		}
	}
	return nil
}

// translatable is prose rather than an identifier.
func translatable(key, value string) bool {
	return strings.TrimSpace(value) != "" && !machineKeys[key]
}

// collectText walks the plan and lists every distinct piece of prose in it.
func collectText(node any, parentKey string, seen map[string]bool, out []string) []string {
	switch value := node.(type) {
	case map[string]any:
		for _, key := range keysSorted(value) {
			if text, ok := value[key].(string); ok {
				if translatable(key, text) && !seen[text] {
					seen[text] = true
					out = append(out, text)
				}
				continue
			}
			out = collectText(value[key], key, seen, out)
		}
	case []any:
		for _, item := range value {
			if text, ok := item.(string); ok {
				if translatable(parentKey, text) && !seen[text] {
					seen[text] = true
					out = append(out, text)
				}
				continue
			}
			out = collectText(item, parentKey, seen, out)
		}
	}
	return out
}

// applyText rebuilds the plan with each piece of prose replaced.
func applyText(node any, parentKey string, byText map[string]string) any {
	switch value := node.(type) {
	case map[string]any:
		out := make(map[string]any, len(value))
		for key, child := range value {
			if text, ok := child.(string); ok && translatable(key, text) {
				out[key] = firstNonEmpty(byText[text], text)
				continue
			}
			out[key] = applyText(child, key, byText)
		}
		return out
	case []any:
		out := make([]any, 0, len(value))
		for _, item := range value {
			if text, ok := item.(string); ok && translatable(parentKey, text) {
				out = append(out, firstNonEmpty(byText[text], text))
				continue
			}
			out = append(out, applyText(item, parentKey, byText))
		}
		return out
	}
	return node
}
