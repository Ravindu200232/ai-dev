package srs

import (
	"context"
	"fmt"
	"regexp"
	"strings"
)

// The interview is a script, not a conversation: 41 topics, each gated by what
// the customer has already said. The gates were Python lambdas; the extraction
// kept them as their source text, and the registry below is the whole set —
// nineteen expressions, resolved by name.

// MaxVisibleQuestions caps how much is asked before the plan is written. Past
// this the answers stop improving and the customer stops answering.
const MaxVisibleQuestions = 25

// The three archetypes a topic can be gated to.
const (
	archetypeCRUD    = "fullstack-crud"
	archetypeLanding = "landing-single-page"
	archetypeTool    = "single-tool"
)

// --- session accessors -------------------------------------------------------

// answerOf reads one recorded answer.
func answerOf(s *Session, key string) any {
	if s == nil || s.Answers == nil {
		return nil
	}
	entry, ok := s.Answers[key]
	if !ok {
		return nil
	}
	return entry.Value
}

func packOf(s *Session) map[string]any {
	if s == nil || s.Pack == nil {
		return map[string]any{}
	}
	return s.Pack
}

func archetypeOf(s *Session) string {
	if v, ok := packOf(s)["archetype"].(string); ok && v != "" {
		return v
	}
	return archetypeCRUD
}

// truthy is how the Python read a yes: the boolean, or any of its spellings.
func truthy(v any) bool {
	switch value := v.(type) {
	case bool:
		return value
	case string:
		switch strings.ToLower(strings.TrimSpace(value)) {
		case "yes", "true", "1":
			return true
		}
	case float64:
		return value == 1
	case int:
		return value == 1
	}
	return false
}

// hasAuth defaults to the app type's own answer when nothing was asked yet.
func hasAuth(s *Session) bool {
	v := answerOf(s, "auth")
	if v == nil {
		if def, ok := packOf(s)["auth_default"].(bool); ok {
			return def
		}
		return true
	}
	return truthy(v)
}

func wantsImages(s *Session) bool { return truthy(answerOf(s, "images")) }

// wantsLeads defaults to true: a landing page with no way to get in touch is
// rarely what was meant.
func wantsLeads(s *Session) bool {
	v := answerOf(s, "lead_capture")
	if v == nil {
		return true
	}
	return truthy(v)
}

func openRegistration(s *Session) bool {
	mode := strings.ToLower(fmt.Sprint(firstAnswer(s, "account_creation", "saas_signup")))
	return mode == "open"
}

func firstAnswer(s *Session, keys ...string) any {
	for _, key := range keys {
		if v := answerOf(s, key); v != nil {
			return v
		}
	}
	return ""
}

func stringList(v any) []string {
	switch value := v.(type) {
	case nil:
		return nil
	case string:
		if value == "" {
			return nil
		}
		return []string{value}
	case []string:
		return value
	case []any:
		out := make([]string, 0, len(value))
		for _, item := range value {
			out = append(out, fmt.Sprint(item))
		}
		return out
	}
	return nil
}

func rolesList(s *Session) []string  { return stringList(answerOf(s, "auth_roles")) }
func tablesList(s *Session) []string { return stringList(answerOf(s, "data_tables")) }

// privilegedSignup are the roles that must never be handed out by a public
// sign-up form.
var privilegedSignup = []string{"admin", "manager", "owner", "staff", "cashier", "operator", "moderator"}

var nonWord = regexp.MustCompile(`[^a-z0-9]+`)

func snakeValue(text string) string {
	s := strings.Trim(nonWord.ReplaceAllString(strings.ToLower(text), "_"), "_")
	if s == "" {
		return "item"
	}
	return s
}

// selfSignupRoles is the subset safe to offer on a public registration form.
func selfSignupRoles(s *Session) []string {
	var out []string
	for _, role := range rolesList(s) {
		key := snakeValue(role)
		privileged := false
		for _, word := range privilegedSignup {
			if strings.Contains(key, word) {
				privileged = true
				break
			}
		}
		if !privileged {
			out = append(out, role)
		}
	}
	return out
}

func packList(s *Session, key string) []string {
	return stringList(packOf(s)[key])
}

// --- the predicate registry ---------------------------------------------------
//
// Each key is the exact source expression the Python topic carried. A topic
// naming an expression that is not here is a bug in the extraction, and
// LoadTopics refuses it rather than silently asking the wrong questions.

type gateFunc func(*Session) bool
type optionsFunc func(*Session, string) []Option
type repeatFunc func(*Session) []string

func onlyFor(kinds ...string) gateFunc {
	return func(s *Session) bool {
		current := archetypeOf(s)
		for _, k := range kinds {
			if current == k {
				return true
			}
		}
		return false
	}
}

func allOf(conditions ...gateFunc) gateFunc {
	return func(s *Session) bool {
		for _, cond := range conditions {
			if !cond(s) {
				return false
			}
		}
		return true
	}
}

func not(cond gateFunc) gateFunc {
	return func(s *Session) bool { return !cond(s) }
}

var gates = map[string]gateFunc{
	"only_for(CRUD)":                 onlyFor(archetypeCRUD),
	"only_for(LANDING)":              onlyFor(archetypeLanding),
	"only_for(TOOL)":                 onlyFor(archetypeTool),
	"wants_images":                   wantsImages,
	"_and(only_for(CRUD), has_auth)": allOf(onlyFor(archetypeCRUD), hasAuth),
	"_and(only_for(CRUD), has_auth, open_registration)": allOf(onlyFor(archetypeCRUD), hasAuth, openRegistration),
	"_and(only_for(CRUD), lambda s: not has_auth(s))":   allOf(onlyFor(archetypeCRUD), not(hasAuth)),
	"_and(only_for(LANDING), wants_leads)":              allOf(onlyFor(archetypeLanding), wantsLeads),
}

// labelled turns a list of values into options with readable labels.
func labelled(values []string, valueOf func(string) string) []Option {
	out := make([]Option, 0, len(values))
	for _, v := range values {
		value := v
		if valueOf != nil {
			value = valueOf(v)
		}
		out = append(out, Option{Label: v, Value: value})
	}
	return out
}

func titleCase(s string) string {
	words := strings.Fields(strings.ReplaceAll(s, "_", " "))
	for i, w := range words {
		if w != "" {
			words[i] = strings.ToUpper(w[:1]) + w[1:]
		}
	}
	return strings.Join(words, " ")
}

var sources = map[string]optionsFunc{
	"lambda s, item: _app_type_options(s)": func(*Session, string) []Option {
		return AppTypeOptions()
	},
	"lambda s, item: [{'label': e, 'value': e} for e in _pack(s).get('entities') or []]": func(s *Session, _ string) []Option {
		return labelled(packList(s, "entities"), nil)
	},
	"lambda s, item: [{'label': f, 'value': f} for f in _pack(s).get('features') or []]": func(s *Session, _ string) []Option {
		return labelled(packList(s, "features"), nil)
	},
	"lambda s, item: [{'label': f, 'value': _snake_value(f)} for f in _pack(s).get('features') or []]": func(s *Session, _ string) []Option {
		return labelled(packList(s, "features"), snakeValue)
	},
	"lambda s, item: [{'label': r.replace('_', ' ').title(), 'value': r} for r in _pack(s).get('roles') or ['admin', 'user']]": func(s *Session, _ string) []Option {
		roles := packList(s, "roles")
		if len(roles) == 0 {
			roles = []string{"admin", "user"}
		}
		out := make([]Option, 0, len(roles))
		for _, r := range roles {
			out = append(out, Option{Label: titleCase(r), Value: r})
		}
		return out
	},
	"lambda s, item: [{'label': r.replace('_', ' ').title(), 'value': r} for r in self_signup_roles(s)]": func(s *Session, _ string) []Option {
		out := []Option{}
		for _, r := range selfSignupRoles(s) {
			out = append(out, Option{Label: titleCase(r), Value: r})
		}
		return out
	},
	// Deliberately empty, so the model reads the customer's own words instead
	// of picking from a list we invented.
	"_offering_options": func(*Session, string) []Option { return nil },
	"_sections_options": sectionOptions,
	"_field_options":    fieldOptions,
}

var repeats = map[string]repeatFunc{
	"roles_list":  rolesList,
	"tables_list": tablesList,
}

// sectionOptions is every band the app type's own page plan is built from.
func sectionOptions(s *Session, _ string) []Option {
	var seen []string
	known := map[string]bool{}
	pages, _ := packOf(s)["pages"].([]any)
	for _, raw := range pages {
		page, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		for _, name := range stringList(page["sections"]) {
			if !known[name] {
				known[name] = true
				seen = append(seen, name)
			}
		}
	}
	return labelled(seen, nil)
}

// boringFields are the ones every table has, which nobody needs to be asked about.
var boringFields = map[string]bool{
	"id": true, "created_at": true, "updated_at": true, "deleted_at": true,
}

var idSuffix = regexp.MustCompile(`_(id|at)$`)

// fieldOptions offers the real columns of the domain table this question is
// about, so the customer confirms rather than invents.
func fieldOptions(s *Session, subject string) []Option {
	table := matchDomainTable(s, subject)
	if table == nil {
		return nil
	}
	var out []Option
	for _, f := range table.Fields {
		if f.Name == "" || boringFields[f.Name] || f.PrimaryKey {
			continue
		}
		label := titleCase(idSuffix.ReplaceAllString(f.Name, ""))
		if label == "" {
			label = f.Name
		}
		option := Option{Label: label, Value: f.Name}
		switch {
		case f.Type == "foreign_key":
			option.Hint = "links to another record"
		case len(f.Values) > 0:
			parts := make([]string, 0, len(f.Values))
			for _, v := range f.Values {
				parts = append(parts, fmt.Sprint(v))
			}
			option.Hint = strings.Join(parts, " / ")
		}
		out = append(out, option)
	}
	return out
}

// matchDomainTable finds the template table a subject is talking about.
func matchDomainTable(s *Session, subject string) *Table {
	want := snakeValue(subject)
	if want == "" {
		return nil
	}
	tables := domainTables(s)
	for i := range tables {
		name := snakeValue(tables[i].TableName)
		if name == want || strings.HasPrefix(name, want) || strings.HasPrefix(want, singular(name)) {
			return &tables[i]
		}
	}
	return nil
}

func domainTables(s *Session) []Table {
	raw, _ := packOf(s)["domain_tables"].([]any)
	out := make([]Table, 0, len(raw))
	for _, item := range raw {
		var t Table
		if doc, ok := item.(map[string]any); ok && fromDoc(doc, &t) == nil {
			out = append(out, t)
		}
	}
	return out
}

// singular is the crude de-pluralisation the matcher needs; it only has to be
// right often enough to line a question up with a table.
func singular(name string) string {
	for _, suffix := range []string{"sses", "xes", "zes", "ches", "shes"} {
		if strings.HasSuffix(name, suffix) {
			return strings.TrimSuffix(name, "es")
		}
	}
	if strings.HasSuffix(name, "ies") {
		return strings.TrimSuffix(name, "ies") + "y"
	}
	if strings.HasSuffix(name, "s") && !strings.HasSuffix(name, "ss") {
		return strings.TrimSuffix(name, "s")
	}
	return name
}

// --- the queue -------------------------------------------------------------------

// Topic is one resolved question template, with its gates already looked up.
type Topic struct {
	Def     TopicDef
	Gate    gateFunc
	Options optionsFunc
	Repeat  repeatFunc
}

// LoadTopics resolves every topic's predicates, refusing any expression the
// registry does not know rather than quietly skipping the question.
func LoadTopics() ([]Topic, error) {
	defs := Knowledge().Topics
	out := make([]Topic, 0, len(defs))
	for _, def := range defs {
		t := Topic{Def: def}
		if def.AppliesTo != "" {
			gate, ok := gates[def.AppliesTo]
			if !ok {
				return nil, fmt.Errorf("topic %q has an unknown gate: %s", def.Key, def.AppliesTo)
			}
			t.Gate = gate
		}
		if def.OptionsFrom != "" {
			source, ok := sources[def.OptionsFrom]
			if !ok {
				return nil, fmt.Errorf("topic %q has an unknown option source: %s", def.Key, def.OptionsFrom)
			}
			t.Options = source
		}
		if def.RepeatsOver != "" {
			repeat, ok := repeats[def.RepeatsOver]
			if !ok {
				return nil, fmt.Errorf("topic %q has an unknown repeat: %s", def.Key, def.RepeatsOver)
			}
			t.Repeat = repeat
		}
		out = append(out, t)
	}
	return out, nil
}

// Slot is one question the queue will ask: a topic, and the thing it is about
// when the topic repeats over roles or records.
type Slot struct {
	Topic   Topic
	Subject string
	Key     string
}

// BuildQueue is the whole scheduling rule. A topic is asked when its gate
// passes and its profile matches; a repeating topic becomes one question per
// role or record.
func BuildQueue(topics []Topic, s *Session) []Slot {
	profile := questionSet(s)
	var queue []Slot

	for _, topic := range topics {
		if topic.Gate != nil && !topic.Gate(s) {
			continue
		}
		if len(topic.Def.Profiles) > 0 && !containsString(topic.Def.Profiles, profile) {
			continue
		}
		if topic.Repeat == nil {
			queue = append(queue, Slot{Topic: topic, Key: topic.Def.Key})
			continue
		}
		for _, subject := range topic.Repeat(s) {
			queue = append(queue, Slot{
				Topic: topic, Subject: subject,
				Key: topic.Def.Key + ":" + snakeValue(subject),
			})
		}
	}
	return queue
}

// questionSet is which profile's questions this session asks.
func questionSet(s *Session) string {
	pack := packOf(s)
	for _, key := range []string{"question_set", "app_type"} {
		if v, ok := pack[key].(string); ok && v != "" {
			return v
		}
	}
	if s != nil && s.AppType != "" {
		return s.AppType
	}
	return "saas"
}

// QuestionBudget keeps the interview to a length someone will finish. Required
// questions are never dropped; optional ones are, oldest first.
func QuestionBudget(queue []Slot) []Slot {
	if len(queue) <= MaxVisibleQuestions {
		return queue
	}
	var required, optional []Slot
	for _, slot := range queue {
		if slot.Topic.Def.Optional {
			optional = append(optional, slot)
		} else {
			required = append(required, slot)
		}
	}
	if len(required) >= MaxVisibleQuestions {
		return required
	}
	return append(required, optional[:MaxVisibleQuestions-len(required)]...)
}

// --- asking ------------------------------------------------------------------------

// Ask renders one slot as the question payload the Studio shows. It carries
// both the current field names and the older ones, because Interview.jsx reads
// several of each.
func (s *Service) Ask(session *Session, slot Slot, index, total int) Question {
	def := slot.Topic.Def

	options := def.FallbackOptions
	if slot.Topic.Options != nil {
		if live := slot.Topic.Options(session, slot.Subject); len(live) > 0 {
			options = live
		} else if def.OptionsLocked {
			options = def.FallbackOptions
		}
	}

	label := def.Label
	prompt := def.Intent
	if slot.Subject != "" {
		label = def.Label + " — " + titleCase(slot.Subject)
		prompt = strings.TrimSpace(def.Intent + " (" + titleCase(slot.Subject) + ")")
	}

	values := make([]string, 0, len(options))
	for _, o := range options {
		values = append(values, o.OptionValue())
	}

	return Question{
		ID: slot.Key, Key: slot.Key,
		Question:         firstNonEmpty(def.Fixed, prompt, label),
		WhyNeeded:        def.Intent,
		AnswerType:       answerTypeFor(def.Kind),
		SuggestedOptions: values,
		MapsToSRSFields:  def.SRSFields,
		CoverageAreas:    def.Coverage,
		Required:         !def.Optional,

		Topic: def.Key, Subject: slot.Subject, Kind: def.Kind,
		Index: index, Total: total, Options: options,
		Placeholder: def.Placeholder, Optional: def.Optional,
		Multiline:      def.Multiline,
		OutputLanguage: session.Language,
	}
}

// answerTypeFor maps a topic kind onto the answer type the Studio switches on.
func answerTypeFor(kind string) string {
	switch kind {
	case "single":
		return "single_choice"
	case "multi":
		return "multi_choice"
	case "yes_no":
		return "yes_no"
	case "number":
		return "number"
	case "upload":
		return "free_text"
	}
	return "free_text"
}

// Record stores one answer against the session.
func Record(session *Session, key string, value any, text string, attachments []string) {
	if session.Answers == nil {
		session.Answers = map[string]AnswerEntry{}
	}
	session.Answers[key] = AnswerEntry{
		Value: value, Text: text, Attachments: attachments, At: NowISO(),
	}
	if !containsString(session.Asked, key) {
		session.Asked = append(session.Asked, key)
	}
}

// FlatAnswers renders the session as the answer list the composer reads.
func FlatAnswers(session *Session) []Answer {
	if session == nil {
		return nil
	}
	out := make([]Answer, 0, len(session.Answers))
	for _, key := range session.Asked {
		entry, ok := session.Answers[key]
		if !ok {
			continue
		}
		out = append(out, Answer{
			QuestionID: key, Value: entry.Value,
			RawText: entry.Text, Attachments: entry.Attachments,
		})
	}
	return out
}

// --- coverage -----------------------------------------------------------------------

// auditNode scores how much of the specification the answers actually cover,
// so the generator knows what it is going to have to assume.
func (s *Service) auditNode(ctx context.Context, state any) (any, error) {
	st := state.(*State)
	coverage := ComputeCoverage(st.Brief, st.Answers)
	st.Coverage = coverage

	score, _ := coverage["score"].(float64)
	s.logf(ctx, st.ProjectID, "CoverageAuditorAgent", 92,
		"Coverage %.0f%% across %d areas", score, len(CoverageAreas))
	return st, nil
}

// ComputeCoverage checks each area against the brief and the answers.
func ComputeCoverage(brief string, answers []Answer) map[string]any {
	said := strings.ToLower(brief)
	for _, a := range answers {
		said += " " + strings.ToLower(fmt.Sprint(a.Value)) + " " + strings.ToLower(a.RawText)
	}

	covered, missing, criticalMissing, optionalMissing := []string{}, []string{}, []string{}, []string{}
	for _, area := range CoverageAreas {
		if CoverageCovered(area, said) {
			covered = append(covered, area)
			continue
		}
		missing = append(missing, area)
		if CriticalAreas[area] {
			criticalMissing = append(criticalMissing, area)
		} else {
			optionalMissing = append(optionalMissing, area)
		}
	}
	score := float64(len(covered)) / float64(len(CoverageAreas)) * 100

	return map[string]any{
		"score": score, "covered": covered, "missing": missing,
		"critical_missing": criticalMissing, "optional_missing": optionalMissing,
		"complete": len(criticalMissing) == 0,
	}
}

func containsString(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}
