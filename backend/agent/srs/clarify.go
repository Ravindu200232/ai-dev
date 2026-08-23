package srs

import (
	"context"
	"encoding/json"
	"regexp"
	"strings"
)

// What did the customer actually mean, and why do they want it.
//
// When someone types two words into a free-text box, those two words become a
// requirement, a table column or a page. Guessing which is how a
// specification ends up describing an app nobody asked for. So a short typed
// answer is read back to them — "you wrote X; which of these did you mean?" —
// and nothing becomes a requirement until both what it is and what it is for
// have been confirmed.

// The states one clarification moves through.
const (
	ClarifyConfirmed      = "confirmed"
	ClarifyNeedsMeaning   = "needs_meaning_confirmation"
	ClarifyNeedsPurpose   = "needs_purpose"
	ClarifyConfirmPurpose = "needs_purpose_confirmation"
	ClarifyDuplicate      = "duplicate"
	ClarifyRejected       = "rejected"
)

// clarifyPrefix marks a clarification's question id apart from an interview
// question's, because the Studio renders them through the same component.
const clarifyPrefix = "clarify:"

const (
	maxClarifyAttempts    = 3
	maxClarifySuggestions = 3
	keepOriginal          = "__keep_original__"
	typeAnother           = "__type_another__"
)

// Clarification is one typed answer being read back.
type Clarification struct {
	QuestionID string `json:"question_id"`
	Topic      string `json:"topic"`
	Question   string `json:"question"`
	ProjectID  string `json:"project_id"`
	Language   string `json:"language"`

	Raw    string `json:"raw"`
	Status string `json:"status"`

	Meaning          string `json:"meaning"`
	MeaningConfirmed bool   `json:"meaning_confirmed"`
	Answer           string `json:"answer,omitempty"`
	Purpose          string `json:"purpose"`
	PurposeConfirmed bool   `json:"purpose_confirmed"`

	Suggestions  []Option         `json:"suggestions"`
	DuplicateOf  string           `json:"duplicate_of"`
	RejectReason string           `json:"reject_reason,omitempty"`
	Attempts     int              `json:"attempts"`
	History      []map[string]any `json:"history"`
}

// Open reports whether this clarification is still waiting on the customer.
func (c *Clarification) Open() bool {
	switch c.Status {
	case ClarifyNeedsMeaning, ClarifyNeedsPurpose, ClarifyConfirmPurpose, ClarifyRejected:
		return true
	}
	return false
}

// Ready is both halves confirmed. Nothing becomes a requirement before this.
func (c *Clarification) Ready() bool {
	if c.Status == ClarifyDuplicate {
		return true
	}
	return c.Status == ClarifyConfirmed && c.MeaningConfirmed && c.PurposeConfirmed
}

// Resolved is the confirmed reading, or nil while anything is still open.
type Resolved struct {
	QuestionID string
	Text       string
	Answer     string
	Raw        string
	Purpose    string
	MergedInto string
}

func (c *Clarification) Resolve() *Resolved {
	if !c.Ready() {
		return nil
	}
	if c.Status == ClarifyDuplicate {
		return &Resolved{QuestionID: c.QuestionID, Text: c.DuplicateOf, Raw: c.Raw,
			Purpose: c.Purpose, MergedInto: c.DuplicateOf}
	}
	text := firstNonEmpty(c.Meaning, c.Raw)
	return &Resolved{QuestionID: c.QuestionID, Text: text,
		Answer: firstNonEmpty(c.Answer, text), Raw: c.Raw, Purpose: c.Purpose}
}

// --- is this answer clear enough to take at face value ---------------------------------

var realWord = regexp.MustCompile(`[^\W\d_]+(?:['’\-][^\W\d_]+)*`)

// filler are the answers that mean "nothing to add". Reading one of those back
// to somebody would be maddening.
var filler = map[string]bool{
	"yes": true, "no": true, "ok": true, "okay": true, "none": true, "n/a": true,
	"na": true, "idk": true, "dunno": true, "maybe": true, "sure": true,
	"any": true, "anything": true, "all": true, "other": true, "etc": true,
	"nothing": true, "nope": true, "not sure": true, "no idea": true,
	"don't know": true,
}

// SelfExplanatory is whether an answer can be taken as written: it is one of
// the options offered, it is filler, or it is a whole phrase rather than a
// fragment.
func SelfExplanatory(text string, options []Option) bool {
	raw := strings.TrimSpace(text)
	if raw == "" {
		return true
	}
	for _, option := range options {
		for _, candidate := range []string{option.Label, option.OptionValue()} {
			if candidate != "" && strings.EqualFold(raw, candidate) {
				return true
			}
		}
	}
	if filler[strings.ToLower(strings.Trim(raw, " .!?,"))] {
		return true
	}
	words := realWord.FindAllString(raw, -1)
	if len(words) == 0 {
		return false
	}
	return len(words) >= 3
}

// NeedsClarification is the inverse, named the way the caller thinks about it.
func NeedsClarification(text string, options []Option) bool {
	return strings.TrimSpace(text) != "" && !SelfExplanatory(text, options)
}

// --- asking the model what it meant ------------------------------------------------------

const clarifySystem = `You interpret short answers written by a NON-TECHNICAL customer describing an app they want built.

Return ONLY a JSON object:
{
  "clear": true or false,
  "meaning": "one plain sentence saying what they are asking for",
  "suggestions": [{"label": "a specific reading of their words", "value": "machine_value"}],
  "duplicate_of": "the exact text of an existing item this repeats, or null",
  "reject_reason": "why this is not a requirement at all, or null"
}

Rules:
- "clear" is true only if there is exactly one sensible reading of their words.
- At most 3 suggestions. Each must be a different reading of what THEY wrote — never a generic feature, never something they did not mention.
- "duplicate_of" is only set when the new text means the same thing as an existing item, not merely something related.
- "reject_reason" is only for input that is not a request at all (random characters, a greeting, a question aimed at you).
- Plain language. No jargon: say "records" not "entities", "log in" not "authentication".
- Never invent scope. If they wrote two words, do not return a paragraph.`

const purposeSystem = `You check whether a customer has explained WHY they want something in their app.

Return ONLY a JSON object:
{"sufficient": true or false, "restated": "their reason in one plain sentence", "missing": "what is still unclear, or null"}

"sufficient" is true when the answer says what the feature is FOR — who uses it or what problem it solves. It does not need detail, only a real reason. "I just want it" and "because" are not reasons. Restate what they said; never add a reason they did not give.`

type interpretation struct {
	Clear        bool     `json:"clear"`
	Meaning      string   `json:"meaning"`
	Suggestions  []Option `json:"suggestions"`
	DuplicateOf  string   `json:"duplicate_of"`
	RejectReason string   `json:"reject_reason"`
}

// StartClarification opens a read-back for one typed answer, or confirms it
// straight away when there is nothing ambiguous about it.
func (s *Service) StartClarification(ctx context.Context, questionID, raw, topic, question string,
	existing []string, options []Option, projectID, customerWords, language string) *Clarification {

	raw = strings.TrimSpace(raw)
	if topic == "" {
		topic = strings.SplitN(questionID, ":", 2)[0]
	}
	state := &Clarification{
		QuestionID: questionID, Topic: topic, Question: question,
		ProjectID: projectID, Language: firstNonEmpty(language, "English"),
		Raw: raw, Status: ClarifyConfirmed,
		Meaning: raw, MeaningConfirmed: true, PurposeConfirmed: true,
		Suggestions: []Option{}, History: []map[string]any{},
	}
	if SelfExplanatory(raw, options) {
		return state
	}
	state.Status, state.Meaning = ClarifyNeedsMeaning, ""
	state.MeaningConfirmed, state.PurposeConfirmed = false, false
	s.interpretInto(ctx, state, existing, customerWords)
	return state
}

// interpretInto asks what the text might mean and folds the reading in.
func (s *Service) interpretInto(ctx context.Context, state *Clarification,
	existing []string, customerWords string) {

	data := s.interpret(ctx, state.Raw, state.Question, existing, state.ProjectID,
		customerWords, state.Language)

	if match := matchesExisting(data.DuplicateOf, existing); match != "" {
		state.Status, state.DuplicateOf, state.Meaning = ClarifyDuplicate, match, match
		state.MeaningConfirmed, state.PurposeConfirmed = true, true
		return
	}

	if reason := strings.TrimSpace(data.RejectReason); reason != "" {
		state.Attempts++
		state.History = append(state.History, map[string]any{"stage": "meaning", "rejected": reason})
		if state.Attempts >= maxClarifyAttempts {
			// Asking a fourth time is worse than taking them at their word.
			state.Status, state.Meaning, state.MeaningConfirmed = ClarifyNeedsPurpose, state.Raw, true
			return
		}
		state.Status, state.RejectReason, state.Suggestions = ClarifyRejected, reason, []Option{}
		return
	}

	suggestions := cleanSuggestions(data.Suggestions, data.Meaning)
	if len(suggestions) == 0 {
		// Nothing to choose between, so there was nothing ambiguous after all.
		state.Status, state.Meaning = ClarifyConfirmed, state.Raw
		state.MeaningConfirmed, state.PurposeConfirmed = true, true
		return
	}
	state.Status, state.Suggestions = ClarifyNeedsMeaning, suggestions
}

func (s *Service) interpret(ctx context.Context, raw, question string, existing []string,
	projectID, customerWords, language string) interpretation {

	known := []string{}
	for _, item := range existing {
		if strings.TrimSpace(item) != "" && len(known) < 20 {
			known = append(known, item)
		}
	}
	knownJSON := "(nothing yet)"
	if len(known) > 0 {
		knownJSON = jsonLine(known)
	}
	preamble := ""
	if customerWords != "" {
		preamble = customerWords + "\n\n"
	}
	user := preamble + "The customer was asked: " +
		firstNonEmpty(question, "(a question about their app)") + "\n\n" +
		"They typed this answer, exactly as written:\n\"\"\"" + raw + "\"\"\"\n\n" +
		"Things they have already asked for:\n" + knownJSON + "\n\n" +
		"Interpret their answer now, in the context of the app they are describing."

	var data interpretation
	system := clarifySystem + outputLanguageRule(language, "clarification response")
	if err := s.LLM.JSON(ctx, roleSRS, system, user, &data); err != nil {
		// No reading is available, which the caller reads as "take it as written".
		return interpretation{}
	}
	return data
}

func cleanSuggestions(raw []Option, meaning string) []Option {
	out := []Option{}
	seen := map[string]bool{}
	for _, item := range raw {
		label := strings.TrimSpace(item.Label)
		if label == "" || seen[strings.ToLower(label)] {
			continue
		}
		seen[strings.ToLower(label)] = true
		value := strings.TrimSpace(item.OptionValue())
		if value == "" || value == "<nil>" {
			value = label
		}
		out = append(out, Option{Label: label, Value: value})
		if len(out) >= maxClarifySuggestions {
			break
		}
	}
	if len(out) == 0 && strings.TrimSpace(meaning) != "" {
		meaning = strings.TrimSpace(meaning)
		out = append(out, Option{Label: meaning, Value: meaning})
	}
	return out
}

// --- the question on screen ---------------------------------------------------------------

// ClarifyQuestion is what the Studio shows for whichever step this state is
// waiting on. It is shaped like an interview question because it is rendered
// by the same component — but it does not count toward the total, since being
// asked to explain yourself is not progress through the interview.
func (c *Clarification) ClarifyQuestion() *Question {
	if !c.Open() {
		return nil
	}
	key := clarifyPrefix + c.QuestionID
	base := Question{
		ID: key, Key: key, Topic: c.Topic,
		WhyNeeded:       "Confirming what you typed before it becomes a requirement.",
		MapsToSRSFields: []string{}, CoverageAreas: []string{},
		Prefill: []string{}, OutputLanguage: c.Language,
	}

	switch c.Status {
	case ClarifyNeedsMeaning:
		options := append([]Option{}, c.Suggestions...)
		options = append(options,
			Option{Label: "Keep my original answer: “" + c.Raw + "”", Value: keepOriginal},
			Option{Label: "Let me type it another way", Value: typeAnother})
		return shapeClarify(base, "single",
			"You wrote “"+c.Raw+"”. Which of these did you mean?", options, "")

	case ClarifyRejected:
		return shapeClarify(base, "text",
			"We could not tell what “"+c.Raw+"” should do in your app. "+
				"Could you say it another way?", nil, "What should it do?")

	case ClarifyNeedsPurpose:
		return shapeClarify(base, "text", "What is “"+c.Meaning+"” for?", nil,
			"Who uses it, or what problem it solves")
	}

	return shapeClarify(base, "yes_no",
		"So: "+c.Meaning+" — "+c.Purpose+". Have we got that right?",
		[]Option{
			{Label: "Yes, that's right", Value: true},
			{Label: "No, let me explain again", Value: false},
		}, "")
}

func shapeClarify(base Question, kind, text string, options []Option, placeholder string) *Question {
	out := base
	out.Kind, out.AnswerType = kind, answerTypeFor(kind)
	out.Question, out.Placeholder = text, placeholder
	out.Options = options
	if out.Options == nil {
		out.Options = []Option{}
	}
	out.SuggestedOptions = []string{}
	for _, o := range out.Options {
		out.SuggestedOptions = append(out.SuggestedOptions, o.Label)
	}
	return &out
}

// --- advancing a step ----------------------------------------------------------------------

// AnswerClarification takes one step forward, whichever step is open.
func (s *Service) AnswerClarification(ctx context.Context, state *Clarification,
	value any, text string, existing []string, customerWords string) {

	typed := strings.TrimSpace(text)
	switch state.Status {
	case ClarifyNeedsMeaning:
		s.answerMeaning(ctx, state, value, typed, existing, customerWords)
	case ClarifyRejected:
		s.retryMeaning(ctx, state, typed, existing, customerWords)
	case ClarifyNeedsPurpose:
		s.answerPurpose(ctx, state, typed, customerWords)
	case ClarifyConfirmPurpose:
		answerPurposeConfirmation(state, value, typed)
	}
}

func (s *Service) answerMeaning(ctx context.Context, state *Clarification,
	value any, typed string, existing []string, customerWords string) {

	choice := ""
	if value != nil {
		choice = firstText(value)
	}
	if choice == typeAnother || (choice == "" && typed != "") {
		s.retryMeaning(ctx, state, typed, existing, customerWords)
		return
	}

	if choice == keepOriginal || choice == "" {
		state.Meaning, state.Answer = state.Raw, state.Raw
	} else {
		var hit *Option
		for i, suggestion := range state.Suggestions {
			if choice == suggestion.OptionValue() || choice == suggestion.Label {
				hit = &state.Suggestions[i]
				break
			}
		}
		label, machine := "", ""
		if hit != nil {
			label, machine = hit.Label, firstNonEmpty(hit.OptionValue(), hit.Label)
		}
		state.Meaning = firstNonEmpty(label, typed, choice)
		state.Answer = cleanAnswer(firstNonEmpty(machine, typed, choice),
			state.QuestionID, firstNonEmpty(typed, state.Raw))
	}

	state.MeaningConfirmed, state.Status = true, ClarifyNeedsPurpose
	state.History = append(state.History, map[string]any{"stage": "meaning", "chose": state.Meaning})
}

func (s *Service) retryMeaning(ctx context.Context, state *Clarification,
	typed string, existing []string, customerWords string) {

	if typed == "" {
		return
	}
	state.History = append(state.History, map[string]any{"stage": "meaning", "retyped": typed})
	probe := *state
	probe.Raw = typed
	s.interpretInto(ctx, &probe, existing, customerWords)

	state.Status, state.Attempts = probe.Status, probe.Attempts
	state.Suggestions, state.DuplicateOf = probe.Suggestions, probe.DuplicateOf
	state.Meaning = probe.Meaning
	state.MeaningConfirmed, state.PurposeConfirmed = probe.MeaningConfirmed, probe.PurposeConfirmed
	state.RejectReason = probe.RejectReason
}

func (s *Service) answerPurpose(ctx context.Context, state *Clarification,
	typed, customerWords string) {

	if typed == "" {
		return
	}
	state.Attempts++
	verdict := s.checkPurpose(ctx, state.Meaning, typed, state.ProjectID, customerWords, state.Language)

	if !verdict.Sufficient && state.Attempts < maxClarifyAttempts {
		state.History = append(state.History, map[string]any{"stage": "purpose",
			"missing": firstNonEmpty(verdict.Missing, "no reason given")})
		state.Purpose, state.Status = typed, ClarifyNeedsPurpose
		return
	}
	state.Purpose = firstNonEmpty(strings.TrimSpace(verdict.Restated), typed)
	state.Status = ClarifyConfirmPurpose
	state.History = append(state.History, map[string]any{"stage": "purpose", "said": state.Purpose})
}

type purposeVerdict struct {
	Sufficient bool   `json:"sufficient"`
	Restated   string `json:"restated"`
	Missing    string `json:"missing"`
}

func (s *Service) checkPurpose(ctx context.Context, meaning, purpose, projectID,
	customerWords, language string) purposeVerdict {

	preamble := ""
	if customerWords != "" {
		preamble = customerWords + "\n\n"
	}
	var data purposeVerdict
	system := purposeSystem + outputLanguageRule(language, "clarification response")
	user := preamble + "They want: " + meaning + "\nTheir reason: \"\"\"" + purpose + "\"\"\""
	if err := s.LLM.JSON(ctx, roleSRS, system, user, &data); err != nil {
		// With nobody to check the reason, the customer's word stands.
		return purposeVerdict{Sufficient: true, Restated: purpose}
	}
	return data
}

func answerPurposeConfirmation(state *Clarification, value any, typed string) {
	if truthy(value) {
		state.PurposeConfirmed, state.Status = true, ClarifyConfirmed
		return
	}
	state.PurposeConfirmed, state.Purpose, state.Status = false, typed, ClarifyNeedsPurpose
	state.History = append(state.History, map[string]any{"stage": "purpose", "rejected": true})
}

// cleanAnswer pulls the bare answer out of a suggestion's machine value. A
// model asked for a value often returns `topic_the_answer`, and storing that
// would put a slug where the customer's words belong.
func cleanAnswer(value, questionID, raw string) string {
	text := strings.TrimSpace(value)
	qualified := false
	if key := strings.TrimSpace(questionID); key != "" {
		for _, sep := range []string{":", "_", "-"} {
			prefix := strings.ToLower(key) + sep
			if strings.HasPrefix(strings.ToLower(text), prefix) {
				text = strings.TrimSpace(text[len(prefix):])
				qualified = true
				break
			}
		}
	}
	text = strings.TrimSpace(strings.Trim(text, `"'“”‘’ `))
	if qualified && raw != "" && looksMachineMade(text) {
		return strings.TrimSpace(raw)
	}
	return text
}

// looksMachineMade is a slug: no spaces, joined the way an identifier is.
func looksMachineMade(text string) bool {
	return text != "" && !strings.Contains(text, " ") &&
		(strings.Contains(text, "_") || strings.Contains(text, "-"))
}

// matchesExisting is the existing item this text names, compared on its words
// so wording differences do not hide a duplicate.
func matchesExisting(text string, existing []string) string {
	target := wordKey(text)
	if target == "" {
		return ""
	}
	for _, item := range existing {
		if wordKey(item) == target {
			return item
		}
	}
	return ""
}

func wordKey(text string) string {
	words := realWord.FindAllString(text, -1)
	for i, w := range words {
		words[i] = strings.ToLower(w)
	}
	sortStrings(words)
	return strings.Join(words, " ")
}

// clarifyStates is how a session carries its open clarifications. They live in
// the session document rather than in memory so a reload does not lose one.
func clarifyStates(session *Session) map[string]*Clarification {
	out := map[string]*Clarification{}
	if session == nil || len(session.Clarifications) == 0 {
		return out
	}
	for key, raw := range session.Clarifications {
		body, err := json.Marshal(raw)
		if err != nil {
			continue
		}
		var state Clarification
		if err := json.Unmarshal(body, &state); err == nil {
			out[key] = &state
		}
	}
	return out
}

func storeClarification(session *Session, key string, state *Clarification) {
	if session.Clarifications == nil {
		session.Clarifications = map[string]any{}
	}
	session.Clarifications[key] = asDoc(state)
}

// PendingClarification is the first one still waiting on the customer.
func PendingClarification(session *Session) (string, *Clarification) {
	states := clarifyStates(session)
	for _, key := range keysSorted(states) {
		if states[key].Open() {
			return key, states[key]
		}
	}
	return "", nil
}
