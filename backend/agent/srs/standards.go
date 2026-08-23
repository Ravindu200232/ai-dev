package srs

import (
	"regexp"
	"strings"
	"time"
)

// The ISO/IEC/IEEE 29148 profile. It adds provenance, a verification method and
// a wording lint to every requirement, and states plainly that structure
// aligned to a standard is not the same thing as certification against it.

var vagueWording = regexp.MustCompile(`(?i)\b(user[- ]?friendly|easy|fast|quickly|appropriate|adequate|etc\.?|and so on|as needed|normally|usually)\b`)
var compoundWording = regexp.MustCompile(`(?i)\b(and|or)\b`)
var testableWording = regexp.MustCompile(`(?i)\b(within|less than|more than|at least|at most|%|seconds?|minutes?|milliseconds?|must|shall|will)\b`)
var shallWord = regexp.MustCompile(`(?i)\bshall\b`)
var idPunctuation = regexp.MustCompile(`[^A-Z0-9]+`)

// verificationMethod is how this requirement would actually be proven.
func verificationMethod(text, category string) string {
	low := strings.ToLower(category + " " + text)
	groups := []struct {
		method string
		words  []string
	}{
		{"Test / Measurement", []string{"performance", "latency", "response time", "throughput", "load", "concurrent"}},
		{"Test / Inspection", []string{"security", "permission", "role", "encrypt", "authentication", "authorization", "privacy"}},
		{"Test / Analysis", []string{"availability", "uptime", "recovery", "backup"}},
		{"Demonstration / Inspection", []string{"ui", "screen", "display", "show", "render", "accessible", "wcag"}},
	}
	for _, group := range groups {
		for _, word := range group.words {
			if strings.Contains(low, word) {
				return group.method
			}
		}
	}
	return "Functional Test"
}

// qualityWarnings are conservative lint findings, not a certification score.
func qualityWarnings(text string) []string {
	s := strings.Join(strings.Fields(text), " ")
	if s == "" {
		return []string{"empty"}
	}
	var flags []string
	if vagueWording.MatchString(s) {
		flags = append(flags, "potentially ambiguous wording")
	}
	if len(compoundWording.FindAllString(s, -1)) >= 3 {
		flags = append(flags, "possibly compound requirement")
	}
	if !testableWording.MatchString(s) && !shallWord.MatchString(s) {
		flags = append(flags, "verification criterion should be made explicit")
	}
	return flags
}

func reviewOf(flags []string) *QualityReview {
	status := "pass"
	if len(flags) > 0 {
		status = "review"
	}
	if flags == nil {
		flags = []string{}
	}
	return &QualityReview{Status: status, Warnings: flags}
}

// ApplyInternationalProfile fills in the standards metadata, in place.
func ApplyInternationalProfile(doc *Document) {
	std := Knowledge().Standards
	doc.StandardsProfile = map[string]any{
		"requirements_standard":       std.SRSStandard,
		"requirements_standard_title": std.SRSStandardTitle,
		"uml_standard":                std.UMLStandard,
		"bpmn_standard":               std.BPMNStandard,
		"erd_notation":                std.ERDNotation,
		"dfd_notation":                std.DFDNotation,
		"conformance_note": "Structure and notation are aligned to the named standards. " +
			"Formal conformance or certification requires project-specific review and " +
			"approval by the responsible organisation.",
	}

	today := time.Now().UTC().Format("2006-01-02")
	if doc.DocumentControl == nil {
		doc.DocumentControl = map[string]any{}
	}
	setDefault(doc.DocumentControl, "document_id", documentID(doc.ProjectName))
	setDefault(doc.DocumentControl, "version", firstNonEmpty(doc.Version, "1.0.0"))
	setDefault(doc.DocumentControl, "status", "Draft")
	setDefault(doc.DocumentControl, "prepared_date", today)
	setDefault(doc.DocumentControl, "standard", std.SRSStandard)
	setDefault(doc.DocumentControl, "document_owner", "Project Stakeholders")

	if doc.RevisionHistory == nil {
		doc.RevisionHistory = []map[string]any{{
			"version":     firstText(doc.DocumentControl["version"]),
			"date":        firstText(doc.DocumentControl["prepared_date"]),
			"description": "Generated requirements baseline",
		}}
	}
	if doc.ApprovalRecord == nil {
		doc.ApprovalRecord = []map[string]any{}
	}

	// A requirement that restates an approved feature says so, which is what
	// makes the traceability matrix worth reading.
	sources := map[string]string{}
	plan := planFromDoc(doc.ApprovedPlan)
	for i, feature := range plan.Features {
		if text := strings.TrimSpace(feature); text != "" {
			sources[strings.ToLower(text)] = "Approved Plan Feature " + itoa(i+1)
		}
	}

	review := []map[string]any{}
	for i := range doc.FunctionalRequirements {
		fr := &doc.FunctionalRequirements[i]
		text := strings.TrimSpace(fr.Requirement)
		if fr.Source == "" {
			fr.Source = firstNonEmpty(sources[strings.ToLower(text)], "Approved SRS / stakeholder input")
		}
		if fr.VerificationMethod == "" {
			fr.VerificationMethod = verificationMethod(text, "functional")
		}
		if fr.Rationale == "" {
			fr.Rationale = "Required to satisfy the approved product capability represented by this requirement."
		}
		flags := qualityWarnings(text)
		fr.QualityReview = reviewOf(flags)
		if len(flags) > 0 {
			review = append(review, map[string]any{"requirement_id": fr.ID, "warnings": flags})
		}
	}
	for i := range doc.NonFunctionalRequirements {
		nfr := &doc.NonFunctionalRequirements[i]
		text := strings.TrimSpace(nfr.Requirement)
		if nfr.Source == "" {
			nfr.Source = "Approved SRS / stakeholder quality expectation"
		}
		if nfr.VerificationMethod == "" {
			nfr.VerificationMethod = verificationMethod(text, nfr.Category)
		}
		flags := qualityWarnings(text)
		nfr.QualityReview = reviewOf(flags)
		if len(flags) > 0 {
			review = append(review, map[string]any{"requirement_id": nfr.ID, "warnings": flags})
		}
	}

	byID := map[string]Requirement{}
	for _, fr := range doc.FunctionalRequirements {
		byID[fr.ID] = fr
	}
	for i := range doc.Traceability {
		row := &doc.Traceability[i]
		fr := byID[row.RequirementID]
		if row.Source == "" {
			row.Source = firstNonEmpty(fr.Source, "Approved SRS")
		}
		if row.VerificationMethod == "" {
			row.VerificationMethod = firstNonEmpty(fr.VerificationMethod, "Functional Test")
		}
		if row.TestCase == "" {
			id := firstNonEmpty(row.RequirementID, "REQ")
			id = strings.TrimPrefix(strings.TrimPrefix(id, "NFR-"), "FR-")
			row.TestCase = "TC-" + id
		}
	}

	doc.RequirementsQualityRev = map[string]any{
		"profile":                    std.SRSStandard,
		"items_needing_human_review": review,
		"note": "Warnings are conservative lint findings, not proof that a " +
			"requirement is invalid.",
	}
}

// documentID is the stable identifier the cover page prints.
func documentID(projectName string) string {
	key := idPunctuation.ReplaceAllString(strings.ToUpper(firstNonEmpty(projectName, "PROJECT")), "-")
	key = strings.Trim(key, "-")
	if len(key) > 32 {
		key = key[:32]
	}
	if key == "" {
		key = "PROJECT"
	}
	return "SRS-" + key
}

func setDefault(m map[string]any, key string, value any) {
	if _, ok := m[key]; !ok {
		m[key] = value
	}
}
