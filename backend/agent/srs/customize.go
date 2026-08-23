package srs

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// Editing a specification that already exists, from a plain-English request.
//
// The rule that shapes this whole file: an edit may never quietly shrink the
// document. A model asked to "add a wishlist" that returns a three-entry
// requirements list has not deleted the other forty — it has answered
// carelessly — so a returned section is folded onto the current one rather
// than replacing it, unless the customer actually asked to remove something.

// derivedSections are rebuilt from the document rather than edited in it. The
// model never sees them and can never write them.
var derivedSections = map[string]bool{
	"diagrams": true, "approved_plan": true, "approved_plan_markdown": true,
	"builder_handoff": true, "branding": true, "effective_plan": true,
}

const editViewBudget = 12000

const customizeSystem = `You edit an existing ISO/IEC/IEEE 29148:2018-aligned SRS JSON based on a user's plain-English request. Apply ONLY what is asked. Return ONLY the top-level sections you actually changed, each one complete — every section you omit is kept from the current document, so never repeat an unchanged section and never return a section as an empty list. The document you are shown may be truncated; edit only what you can see. Return ONLY JSON of the form {"srs_document": {<changed sections only>}, "diff_summary": ["short bullet", ...]}.`

var removalWord = regexp.MustCompile(
	`(?i)\b(remove|delete|drop|exclude|scrap|get rid of|take out|no longer|don'?t need|do not need)\b`)

// docMap is the document as plain JSON, which is what the merge works on: the
// patch names sections by their JSON key, and a typed merge would need one
// branch per section to say the same thing.
func docMap(doc *Document) map[string]any {
	return asDoc(doc)
}

func docFromMap(body map[string]any) (*Document, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	var out Document
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// editableView is what the model is shown: the document without the sections
// it must not touch, trimmed to a size a small model can hold.
func editableView(doc *Document) string {
	body := docMap(doc)
	for key := range derivedSections {
		delete(body, key)
	}
	return truncate(jsonLine(body), editViewBudget)
}

// blank treats an omitted section and an empty one as the same thing: no edit.
func blank(value any) bool {
	switch v := value.(type) {
	case nil:
		return true
	case string:
		return v == ""
	case []any:
		return len(v) == 0
	case map[string]any:
		return len(v) == 0
	}
	return false
}

// identityKeys are the fields that name one entry, in the order they are
// trusted. Two entries with the same identity are the same thing.
var identityKeys = []string{"id", "table_name", "page_name", "role_key",
	"workflow_name", "requirement_id", "role", "name", "field", "area", "category"}

func identityOf(item any) (string, bool) {
	switch v := item.(type) {
	case map[string]any:
		for _, key := range identityKeys {
			if text, ok := v[key].(string); ok && strings.TrimSpace(text) != "" {
				return key + "\x00" + strings.ToLower(strings.TrimSpace(text)), true
			}
		}
		return "", false
	case string:
		return "_text\x00" + strings.ToLower(strings.Join(strings.Fields(v), " ")), true
	}
	return "", false
}

var idKeys = []string{"id", "requirement_id"}
var idShape = regexp.MustCompile(`^([A-Za-z]+-?)(\d+)$`)

// ignoreWords are the words every requirement contains, so they say nothing
// about whether two entries are the same one.
var ignoreWords = map[string]bool{
	"system": true, "shall": true, "user": true, "users": true, "the": true,
	"and": true, "with": true, "that": true, "this": true, "from": true,
	"into": true, "must": true, "able": true, "allow": true, "allows": true,
	"when": true, "their": true, "they": true, "page": true, "data": true,
}

var contentWord = regexp.MustCompile(`[a-z]{4,}`)

func contentWords(item any) map[string]bool {
	text := ""
	switch v := item.(type) {
	case map[string]any:
		var parts []string
		for _, key := range keysSorted(v) {
			if containsString(idKeys, key) {
				continue
			}
			if value, ok := v[key].(string); ok {
				parts = append(parts, value)
			}
		}
		text = strings.Join(parts, " ")
	default:
		text = firstText(item)
	}
	out := map[string]bool{}
	for _, word := range contentWord.FindAllString(strings.ToLower(text), -1) {
		if !ignoreWords[word] {
			out[word] = true
		}
	}
	return out
}

// differentThing is whether two entries that share an id share so little text
// that one cannot be an edit of the other — in which case the id was reused as
// a slot number, not as a name.
func differentThing(incoming, existing any) bool {
	a, okA := incoming.(map[string]any)
	b, okB := existing.(map[string]any)
	if !okA || !okB {
		return false
	}
	hasID := false
	for _, key := range idKeys {
		if strings.TrimSpace(firstText(a[key])) != "" {
			hasID = true
		}
	}
	if !hasID {
		return false
	}
	first, second := contentWords(a), contentWords(b)
	if len(first) == 0 || len(second) == 0 {
		return false
	}
	shared := 0
	for word := range first {
		if second[word] {
			shared++
		}
	}
	union := len(first) + len(second) - shared
	return float64(shared)/float64(union) < 0.25
}

// renumbered gives an entry the next free id in the series it was numbered in,
// so a genuinely new thing does not overwrite the one whose slot it borrowed.
func renumbered(item map[string]any, existing []any) map[string]any {
	key := ""
	for _, candidate := range idKeys {
		if strings.TrimSpace(firstText(item[candidate])) != "" {
			key = candidate
			break
		}
	}
	if key == "" {
		return item
	}
	shape := idShape.FindStringSubmatch(strings.TrimSpace(firstText(item[key])))
	if shape == nil {
		return item
	}
	prefix, digits := shape[1], shape[2]
	highest := 0
	for _, other := range existing {
		row, ok := other.(map[string]any)
		if !ok {
			continue
		}
		got := idShape.FindStringSubmatch(strings.TrimSpace(firstText(row[key])))
		if got == nil || !strings.EqualFold(got[1], prefix) {
			continue
		}
		if n := atoi(got[2]); n > highest {
			highest = n
		}
	}
	number := itoa(highest + 1)
	for len(number) < len(digits) {
		number = "0" + number
	}
	out := map[string]any{}
	for k, v := range item {
		out[k] = v
	}
	out[key] = prefix + number
	return out
}

// mergeList folds the patch's entries onto the current ones. It never returns
// fewer than it was given unless the customer asked for a removal.
func mergeList(incoming, current []any, removing bool) []any {
	if removing || len(incoming) >= len(current) || len(current) == 0 {
		return incoming
	}
	identities := make([]string, len(incoming))
	for i, item := range incoming {
		ident, ok := identityOf(item)
		if !ok {
			// Nothing to match on, so a shorter list cannot be folded in
			// safely and the current one stands.
			return current
		}
		identities[i] = ident
	}

	out := append([]any{}, current...)
	where := map[string]int{}
	for i, item := range out {
		if ident, ok := identityOf(item); ok {
			if _, seen := where[ident]; !seen {
				where[ident] = i
			}
		}
	}
	for i, item := range incoming {
		at, ok := where[identities[i]]
		if !ok {
			out = append(out, item)
			continue
		}
		if differentThing(item, out[at]) {
			if row, isMap := item.(map[string]any); isMap {
				out = append(out, renumbered(row, out))
				continue
			}
			out = append(out, item)
			continue
		}
		out[at] = item
	}
	return out
}

// cleanPatch drops the empty and the protected, and merges the rest.
func cleanPatch(patch, current map[string]any, prompt string) map[string]any {
	removing := removalWord.MatchString(prompt)
	out := map[string]any{}
	for _, key := range keysSorted(patch) {
		value := patch[key]
		if derivedSections[key] || blank(value) {
			continue
		}
		existing := current[key]

		if list, ok := value.([]any); ok {
			if currentList, ok := existing.([]any); ok {
				out[key] = mergeList(list, currentList, removing)
				continue
			}
			out[key] = value
			continue
		}
		if section, ok := value.(map[string]any); ok {
			if currentSection, ok := existing.(map[string]any); ok {
				merged := map[string]any{}
				for k, v := range currentSection {
					merged[k] = v
				}
				for inner, item := range section {
					if list, ok := item.([]any); ok {
						if currentList, ok := currentSection[inner].([]any); ok {
							merged[inner] = mergeList(list, currentList, removing)
							continue
						}
					}
					if !blank(item) {
						merged[inner] = item
					}
				}
				out[key] = merged
				continue
			}
		}
		out[key] = value
	}
	return out
}

// MergeEdit overlays a section patch onto the specification, leaving every
// section the patch did not mention exactly as it was.
func MergeEdit(doc *Document, patch map[string]any, prompt string) (*Document, error) {
	body := docMap(doc)
	for key, value := range cleanPatch(patch, body, prompt) {
		body[key] = value
	}
	return docFromMap(body)
}

// --- the deterministic editor -----------------------------------------------------------

var renameRequest = regexp.MustCompile(`(?i)rename\s+([\w\- ]+?)\s+(?:to|->|→)\s+([\w\- ]+)`)
var approvalRequest = regexp.MustCompile(`(?i)add\s+(?:admin\s+)?approval\s+for\s+([\w\- ]+)`)
var addRequest = regexp.MustCompile(`(?i)add\s+(?:a\s+|an\s+)?(.+?)(?:\s+(?:feature|module|requirement|support))?$`)
var languageName = regexp.MustCompile(`(?i)\b(sinhala|tamil|english|spanish|french|arabic|german|hindi)\b`)
var threeSeconds = regexp.MustCompile(`(?i)\b3\s*seconds?\b`)

// ApplyCustomization is the editor that runs when no model is reachable, or
// when the one that is reachable changed nothing. It reads the request for the
// handful of intents that come up over and over and applies them exactly.
func ApplyCustomization(doc *Document, prompt string) (*Document, []string) {
	low := strings.ToLower(strings.TrimSpace(prompt))
	var diff []string
	out := doc

	if m := renameRequest.FindStringSubmatch(prompt); m != nil {
		old, replacement := strings.TrimSpace(m[1]), strings.TrimSpace(m[2])
		if renamed, err := renameThroughout(doc, old, replacement); err == nil {
			out = renamed
			diff = append(diff, "Renamed '"+old+"' → '"+replacement+"' across the SRS.")
		}
	}

	if !strings.Contains(low, "rename") && containsAny(low, "performance", "p95", "faster", "stricter") {
		changed := false
		for i := range out.NonFunctionalRequirements {
			nfr := &out.NonFunctionalRequirements[i]
			if !strings.EqualFold(nfr.Category, "performance") {
				continue
			}
			nfr.Requirement = threeSeconds.ReplaceAllString(nfr.Requirement, "1 second")
			if !strings.Contains(nfr.Requirement, "P95") {
				nfr.Requirement += " (P95 latency target tightened)."
			}
			changed = true
		}
		if changed {
			diff = append(diff, "Tightened performance NFR to a stricter P95 target.")
		}
	}

	var languages []string
	for _, m := range languageName.FindAllStringSubmatch(low, -1) {
		if name := sentenceCase(m[1]); !containsString(languages, name) {
			languages = append(languages, name)
		}
	}
	sortStrings(languages)
	if strings.Contains(low, "language") || len(languages) > 0 {
		names := languages
		if len(names) == 0 {
			names = []string{"additional languages"}
		}
		if out.UIUX == nil {
			out.UIUX = map[string]any{}
		}
		existing := stringList(out.UIUX["languages"])
		for _, name := range names {
			if !containsString(existing, name) {
				existing = append(existing, name)
			}
		}
		sortStrings(existing)
		out.UIUX["languages"] = existing
		out.addRequirement("Localization", "The system shall support "+
			strings.Join(names, ", ")+" as selectable display languages.", "medium")
		diff = append(diff, "Added language support: "+strings.Join(names, ", ")+".")
	}

	switch {
	case containsAny(low, "currency", "multi-currency", "multicurrency"):
		out.addRequirement("Payments", "The system shall support multiple currencies with "+
			"configurable exchange rates and per-transaction currency selection.", "high")
		diff = append(diff, "Added multi-currency requirement.")
	case containsAny(low, "payment", "payment gateway", "stripe", "checkout"):
		hasGateway := false
		for _, i := range out.IntegrationRequirements {
			if i.Type == "payment" {
				hasGateway = true
			}
		}
		if !hasGateway {
			required := true
			out.IntegrationRequirements = append(out.IntegrationRequirements, Integration{
				Name: "Payment Gateway", Type: "payment",
				Description: "Online card payments via a hosted gateway.", Required: &required,
			})
		}
		out.addRequirement("Payments", "The system shall accept online payments through a "+
			"secure hosted payment gateway.", "high")
		diff = append(diff, "Added online payment gateway requirement.")
	}

	if m := approvalRequest.FindStringSubmatch(low); m != nil {
		thing := strings.TrimSpace(m[1])
		out.BusinessWorkflows = append(out.BusinessWorkflows, Workflow{
			WorkflowName: titleCase(thing) + " Approval Workflow",
			Steps: []string{
				"User requests " + thing,
				"Request enters 'pending approval' state",
				"Admin reviews the request",
				"Admin approves or rejects",
				"On approval the " + thing + " is processed",
				"Requester is notified of the outcome",
			},
		})
		out.ValidationRules = append(out.ValidationRules, ValidationRule{
			Field: strings.ReplaceAll(thing, " ", "_"),
			Rule:  "A " + thing + " requires admin approval before it is finalised.",
		})
		diff = append(diff, "Added admin approval workflow for "+thing+".")
	}

	if containsAny(low, "loyalty", "rewards", "points") {
		if !containsString(out.MainModules, "Loyalty & Rewards") {
			out.MainModules = append(out.MainModules, "Loyalty & Rewards")
		}
		if !hasTable(out.DatabaseDesign.Tables, "loyalty_accounts") {
			no := false
			out.DatabaseDesign.Tables = append(out.DatabaseDesign.Tables, Table{
				TableName: "loyalty_accounts", Description: "Customer loyalty points balance.",
				Fields: []Field{
					{Name: "id", Type: "uuid", PrimaryKey: true, Nullable: &no},
					{Name: "user_id", Type: "foreign_key", References: "users.id", Nullable: &no},
					{Name: "points_balance", Type: "integer", Default: 0, Nullable: &no},
					{Name: "tier", Type: "enum", Values: []any{"bronze", "silver", "gold"},
						Default: "bronze", Nullable: &no},
				},
			})
		}
		out.addRequirement("Loyalty & Rewards", "The system shall award and redeem loyalty "+
			"points and assign membership tiers.", "medium")
		diff = append(diff, "Added loyalty programme (module, table, requirement).")
	}

	if len(diff) > 0 {
		return out, diff
	}

	// Nothing matched, so the request is taken at its word and added as one
	// capability rather than being dropped.
	label := strings.TrimSpace(prompt)
	if m := addRequest.FindStringSubmatch(low); m != nil {
		label = strings.TrimSpace(m[1])
	}
	label = truncate(label, 80)
	module := titleCase(label)
	if !containsString(out.MainModules, module) {
		out.MainModules = append(out.MainModules, module)
	}
	out.addRequirement(module, "The system shall provide "+label+".", "medium")
	return out, []string{"Added requirement & module for: " + label + "."}
}

// addRequirement appends one requirement with the next free id in its series.
func (d *Document) addRequirement(module, text, priority string) {
	highest := 0
	for _, fr := range d.FunctionalRequirements {
		if n := atoi(digitsOnly(fr.ID)); n > highest {
			highest = n
		}
	}
	d.FunctionalRequirements = append(d.FunctionalRequirements, Requirement{
		ID: requirementID("FR", highest+1), Module: module,
		Requirement: text, Priority: priority, AllowedRoles: []string{},
	})
}

// renameThroughout replaces a name everywhere it appears, including inside
// identifiers and descriptions, which is what "rename X to Y" means.
func renameThroughout(doc *Document, old, replacement string) (*Document, error) {
	if strings.TrimSpace(old) == "" {
		return doc, fmt.Errorf("nothing to rename")
	}
	raw, err := json.Marshal(doc)
	if err != nil {
		return doc, err
	}
	pattern, err := regexp.Compile(`(?i)` + regexp.QuoteMeta(old))
	if err != nil {
		return doc, err
	}
	var out Document
	if err := json.Unmarshal(pattern.ReplaceAll(raw, []byte(replacement)), &out); err != nil {
		return doc, err
	}
	return &out, nil
}

func containsAny(text string, needles ...string) bool {
	for _, needle := range needles {
		if strings.Contains(text, needle) {
			return true
		}
	}
	return false
}

func digitsOnly(text string) string {
	var b strings.Builder
	for _, r := range text {
		if r >= '0' && r <= '9' {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func atoi(text string) int {
	n := 0
	for _, r := range text {
		if r < '0' || r > '9' {
			return n
		}
		n = n*10 + int(r-'0')
	}
	return n
}

// --- the node ---------------------------------------------------------------------------

// customizeNode applies one edit. The model is asked first because it can read
// a request the deterministic editor has no rule for; whatever it returns is
// merged, never trusted wholesale, and a model that changes nothing hands over
// to the editor rather than reporting success.
func (s *Service) customizeNode(ctx context.Context, state any) (any, error) {
	st := state.(*State)
	if err := ctx.Err(); err != nil {
		return st, err
	}
	if st.Document == nil {
		return st, fmt.Errorf("there is no specification to edit")
	}
	prompt := strings.TrimSpace(st.CustomizationPrompt)
	s.logf(ctx, st.ProjectID, "CustomizationAgent", 15, "Applying edit: “%s”", prompt)

	var answer struct {
		Document    map[string]any `json:"srs_document"`
		DiffSummary []string       `json:"diff_summary"`
	}
	user := strings.TrimSpace(customerContext(st.Brief, st.Session, st.Project)) +
		"\n\nCURRENT SRS:\n" + editableView(st.Document) +
		"\n\nUSER EDIT REQUEST:\n" + prompt

	err := s.LLM.JSONValid(ctx, roleSRS, customizeSystem+
		"\nOUTPUT LANGUAGE: Return every customer-visible changed SRS value in English.",
		user, &answer, func() error {
			merged, err := MergeEdit(st.Document, answer.Document, prompt)
			if err != nil {
				return err
			}
			return merged.Validate()
		})

	var edited *Document
	var diff []string
	switch {
	case err != nil:
		s.warn(ctx, st.ProjectID, "CustomizationAgent",
			"Model edit unavailable or invalid; using the deterministic editor.", 45)
		edited, diff = ApplyCustomization(st.Document, prompt)

	default:
		patch := cleanPatch(answer.Document, docMap(st.Document), prompt)
		if len(patch) == 0 {
			s.warn(ctx, st.ProjectID, "CustomizationAgent",
				"The model changed no section; using the deterministic editor.", 45)
			edited, diff = ApplyCustomization(st.Document, prompt)
			break
		}
		merged, mergeErr := MergeEdit(st.Document, patch, prompt)
		if mergeErr != nil {
			s.recordError(ctx, st.ProjectID, "CustomizationAgent", mergeErr)
			edited, diff = ApplyCustomization(st.Document, prompt)
			break
		}
		edited = merged
		diff = answer.DiffSummary
		if len(diff) == 0 {
			diff = []string{"Applied requested edit."}
		}
		s.emit(ctx, st.ProjectID, "CustomizationAgent",
			"Edit applied and validated: "+strings.Join(keysSorted(patch), ", ")+".",
			"success", 45, nil)
	}

	edited.DocumentLanguage = "English"
	edited.EffectivePlan = asDoc(EffectivePlan(edited))
	ApplyInternationalProfile(edited)

	st.Document, st.DiffSummary = edited, diff
	s.emit(ctx, st.ProjectID, "CustomizationAgent",
		fmt.Sprintf("Edit applied: %d change(s).", len(diff)), "success", 55,
		map[string]any{"diff": diff})
	return st, nil
}
