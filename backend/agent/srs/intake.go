package srs

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Intake is the gate. Its job is to notice when what arrived is not a product
// idea at all, because everything downstream — the interview, the plan, the
// specification — is wasted on a keyboard mash, and the customer is better told
// so immediately.

var wordRe = regexp.MustCompile(`[A-Za-z]{2,}`)

// common is the vocabulary a real product idea draws on. A brief made almost
// entirely of words outside this set, none of which look like words, is noise.
var common = map[string]bool{}

func init() {
	for _, w := range strings.Fields(`
		the a an and or for to of with in on by that this where who can will
		should have has be is are my our their they it as so from at
		app application system software platform website web site page tool
		service solution product project shop store ecommerce commerce market
		marketplace manage management track tracker tracking monitor book
		booking order orders inventory stock report reports analytics dashboard
		payment payments invoice user users online sell buy build want need
		create customer customers client admin staff portal data hotel school
		student clinic patient vehicle car restaurant delivery event fitness
		gym real estate property rental rent social blog learning course`) {
		common[w] = true
	}
}

// mash are the runs a hand makes walking the keyboard.
var mash = []string{
	"asd", "sdf", "dfg", "fgh", "ghj", "hjk", "jkl", "qwe", "qwer",
	"zxc", "xcv", "cvb", "vbn", "bnm", "qaz", "wsx", "edc", "rfv",
	"tgb", "yhn", "ujm", "poi", "oiu", "iuy", "mnb", "lkj", "kjh",
}

var labelRe = regexp.MustCompile(`^[A-Z][A-Z0-9 ()._\-]*:$`)

// plausibleWord reports whether an unrecognised token still looks like a word.
func plausibleWord(w string) bool {
	vowels := strings.Count(w, "a") + strings.Count(w, "e") + strings.Count(w, "i") +
		strings.Count(w, "o") + strings.Count(w, "u")
	if vowels == 0 {
		return false
	}
	for _, m := range mash {
		if strings.Contains(w, m) {
			return false
		}
	}
	if len(w) >= 6 && strings.Count(w, w[:3]) >= 2 {
		return false
	}
	if hasTripledLetter(w) {
		return false
	}
	ratio := float64(vowels) / float64(len(w))
	return ratio >= 0.15 && ratio <= 0.85 && len(w) <= 20
}

// hasTripledLetter finds "aaa" — Go's regexp has no backreference, and a loop
// says what it means more plainly anyway.
func hasTripledLetter(w string) bool {
	for i := 2; i < len(w); i++ {
		if w[i] == w[i-1] && w[i] == w[i-2] {
			return true
		}
	}
	return false
}

// LooksLikeNonsense is the heuristic gate, and its reason is shown to the user.
func LooksLikeNonsense(text string) (bool, string) {
	t := strings.TrimSpace(text)
	if len(t) < 8 {
		return true, "The idea is too short to understand."
	}
	words := wordRe.FindAllString(strings.ToLower(t), -1)
	if len(words) < 3 {
		return true, "The idea does not contain enough real words."
	}

	recognized, plausible := 0, 0
	distinct := map[string]bool{}
	for _, w := range words {
		distinct[w] = true
		if common[w] {
			recognized++
			continue
		}
		if plausibleWord(w) {
			plausible++
		}
	}
	ratio := float64(recognized+plausible) / float64(len(words))

	const noise = "The idea looks like random characters rather than a software description."
	switch {
	case recognized == 0 && ratio < 0.5:
		return true, noise
	case ratio < 0.4:
		return true, noise
	case len(distinct) <= 2 && len(words) <= 3 && recognized == 0:
		return true, "The idea looks like placeholder text."
	}
	return false, ""
}

// contentOnly strips the section labels a composed brief carries, so
// "USER IDEA:" does not count against the score.
func contentOnly(brief string) string {
	var out []string
	for _, line := range strings.Split(brief, "\n") {
		s := strings.TrimSpace(line)
		if s == "" || s == "---" || labelRe.MatchString(s) {
			continue
		}
		out = append(out, s)
	}
	return strings.Join(out, " ")
}

// DetectLanguage reads the script rather than the words, which is enough to
// know whether the answer has to be translated before the builder sees it.
func DetectLanguage(text string) string {
	for _, r := range text {
		switch {
		case r >= 0x0D80 && r <= 0x0DFF:
			return "Sinhala"
		case r >= 0x0B80 && r <= 0x0BFF:
			return "Tamil"
		case r >= 0x0600 && r <= 0x06FF:
			return "Arabic"
		case r >= 0x4E00 && r <= 0x9FFF:
			return "Chinese"
		}
	}
	return "English"
}

// IsEnglish reports whether a language name needs no translation.
func IsEnglish(language string) bool {
	l := strings.ToLower(strings.TrimSpace(language))
	return l == "" || l == "english" || l == "en"
}

// intakeNode normalises the idea and decides whether it can be worked with.
func (s *Service) intakeNode(ctx context.Context, state any) (any, error) {
	st := state.(*State)
	brief := strings.TrimSpace(firstNonEmpty(st.Brief, st.RawIdea))
	s.logf(ctx, st.ProjectID, "IntakeExtractorAgent", 10, "Reading your idea")

	language := st.Language
	if language == "" {
		language = DetectLanguage(brief)
	}
	nonsense, reason := LooksLikeNonsense(contentOnly(brief))

	if nonsense {
		s.warn(ctx, st.ProjectID, "IntakeExtractorAgent",
			"Input is too vague to generate an SRS — asking for clarification.", 20)
	} else {
		s.emit(ctx, st.ProjectID, "IntakeExtractorAgent",
			"Idea understood; cleaning and normalising.", "info", 20, nil)
	}

	st.Brief, st.Language = brief, language
	st.IsNonsense, st.NeedsClarification, st.ClarificationReason = nonsense, nonsense, reason
	return st, nil
}

// --- classification ---------------------------------------------------------------

const classifySystem = `You are a software domain classifier. Given a product
idea, identify the business domain and application type.

Choose build_category by what the software DOES, not by the industry it serves.
A clinic, a school library or a repair shop that keeps its own records is a
dashboard, not saas. Use "other" only when none fit.

Answer with JSON only:
{"domain_key": one of %s,
 "detected_domain": "short label",
 "app_type": "SPA|POS|admin dashboard|customer portal|PWA|hybrid",
 "build_category": one of %s,
 "build_category_why": "one short clause",
 "confidence": 0.0-1.0,
 "reasoning": "one sentence"}`

// classifyNode names the domain deterministically, then lets the model refine
// it. A model that is offline or wrong never loses the keyword answer.
func (s *Service) classifyNode(ctx context.Context, state any) (any, error) {
	st := state.(*State)
	s.logf(ctx, st.ProjectID, "DomainClassifierAgent", 40, "Identifying domain patterns…")

	key, confidence := ClassifyDomain(st.Brief)
	domain := GetDomain(key)
	guessed, guessConfidence, why := GuessAppType(st.Brief)

	classification := map[string]any{
		"domain_key":                key,
		"detected_domain":           domain.Label,
		"app_type":                  domain.AppTypePrimary,
		"build_category":            guessed,
		"build_category_confidence": guessConfidence,
		"build_category_why":        why,
		"confidence":                confidence,
		"similar_patterns":          similarPatterns(key),
		"reasoning":                 "Matched " + domain.Label + " by keyword evidence in the brief.",
	}

	s.refineClassification(ctx, st, classification)

	final, _ := classification["detected_domain"].(string)
	pct, _ := classification["confidence"].(float64)
	s.emit(ctx, st.ProjectID, "DomainClassifierAgent",
		fmt.Sprintf("Detected %s (confidence %.0f%%)", final, pct*100),
		"success", 60, map[string]any{"classification": classification})

	st.Classification = classification
	if st.Project != nil {
		st.Project.Classification = classification
		st.Project.Complexity = complexityFor(classification["domain_key"].(string), st.Brief)
		st.Project.SuggestedStack = stackFor(classification["domain_key"].(string))
	}
	return st, nil
}

// refineClassification asks the model to improve on the keyword answer, and
// keeps only the fields it is allowed to change.
func (s *Service) refineClassification(ctx context.Context, st *State, into map[string]any) {
	k := Knowledge()
	domains := append(keysSorted(k.Domains), "custom")
	categories := keysSorted(k.AppTypes)
	domainsJSON, _ := json.Marshal(domains)
	categoriesJSON, _ := json.Marshal(categories)

	var reply struct {
		DomainKey        string  `json:"domain_key"`
		DetectedDomain   string  `json:"detected_domain"`
		AppType          string  `json:"app_type"`
		BuildCategory    string  `json:"build_category"`
		BuildCategoryWhy string  `json:"build_category_why"`
		Confidence       float64 `json:"confidence"`
		Reasoning        string  `json:"reasoning"`
	}
	system := fmt.Sprintf(classifySystem, domainsJSON, categoriesJSON)
	user := "Product idea:\n" + truncate(st.Brief, 2500)

	if err := s.LLM.JSON(ctx, roleSRS, system, user, &reply); err != nil {
		s.warn(ctx, st.ProjectID, "DomainClassifierAgent",
			"Ollama offline; using deterministic classification.", 55)
		return
	}
	s.trace(ctx, st.ProjectID, Doc{"label": "domain_classify", "response": reply})

	if _, ok := k.AppTypes[strings.ToLower(reply.BuildCategory)]; ok {
		into["build_category"] = strings.ToLower(reply.BuildCategory)
		into["build_category_why"] = truncate(reply.BuildCategoryWhy, 140)
		if reply.Confidence > 0 {
			into["build_category_confidence"] = reply.Confidence
		}
	}

	_, known := k.Domains[reply.DomainKey]
	if !known && reply.DomainKey != "custom" {
		return
	}
	into["domain_key"] = reply.DomainKey
	if reply.DetectedDomain != "" {
		into["detected_domain"] = reply.DetectedDomain
	} else {
		into["detected_domain"] = GetDomain(reply.DomainKey).Label
	}
	if reply.AppType != "" {
		into["app_type"] = reply.AppType
	}
	if reply.Confidence > 0 {
		into["confidence"] = reply.Confidence
	}
	if reply.Reasoning != "" {
		into["reasoning"] = reply.Reasoning
	}
	s.emit(ctx, st.ProjectID, "DomainClassifierAgent",
		"LLM refined domain → "+fmt.Sprint(into["detected_domain"]), "info", 55, nil)
}

// similarPatterns is how many templates back this classification up.
func similarPatterns(key string) int {
	if key == GenericDomain {
		return 1
	}
	return 3
}

// complexityFor sizes the build from how much the domain template carries.
func complexityFor(domainKey, brief string) map[string]any {
	domain := GetDomain(domainKey)
	score := len(domain.Tables) + len(domain.Modules) + len(brief)/400
	switch {
	case score >= 18:
		return map[string]any{"overall": "High", "backend": "High", "frontend": "Medium"}
	case score >= 12:
		return map[string]any{"overall": "Medium-High", "backend": "High", "frontend": "Medium"}
	}
	return map[string]any{"overall": "Medium", "backend": "Medium", "frontend": "Medium"}
}

// stackFor is fixed — the builder only knows one stack — but the architecture
// note reflects how much the domain has to keep apart.
func stackFor(domainKey string) map[string]any {
	architecture := "Modular Monolith"
	switch domainKey {
	case "retail", "vehicle", "hotel":
		architecture = "Microservices"
	}
	return map[string]any{
		"frontend": "React + Tailwind", "backend": "Express / Node",
		"database": "MongoDB", "architecture": architecture, "locked": true,
	}
}

// --- shared helpers ---------------------------------------------------------------

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

// truncate shortens to n bytes without splitting a character in half, which
// matters because these strings reach prompts and the customer's console.
func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	cut := n
	for cut > 0 && !utf8.RuneStart(s[cut]) {
		cut--
	}
	return s[:cut] + "…"
}

func keysSorted[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sortStrings(out)
	return out
}

func sortStrings(v []string) {
	for i := 1; i < len(v); i++ {
		for j := i; j > 0 && v[j] < v[j-1]; j-- {
			v[j], v[j-1] = v[j-1], v[j]
		}
	}
}
