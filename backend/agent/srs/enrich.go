package srs

import (
	"context"
	"fmt"
	"regexp"
	"strings"
)

// The model's half of the specification. The composer has already produced a
// complete, valid document from the plan; this only sharpens it — better field
// types, better wording, the risks and ambiguities a template cannot know.
//
// Nothing it returns is trusted to widen the document. A table that is not in
// the plan is refused, and a requirement about signing in is refused outright
// when the plan has no accounts, because the customer would be shown a
// specification for an app they did not approve.

const enrichSystem = `You are a senior requirements engineer. Given a non-technical user's app idea and their answers, produce the DOMAIN-SPECIFIC content for a software requirements specification as STRICT JSON (no prose, no markdown). Tailor everything to THIS idea — real tables, real modules, real requirements. Return ONLY a JSON object with these keys:
{
  "system_category": str,
  "app_summary": {"business_goal": str, "short_description": str, "target_users": [str]},
  "roles": [{"role_key": snake_case, "role_name": str, "description": str}],
  "main_modules": [str],
  "tables": [{"table_name": snake_case, "description": str, "fields": [{"name": str, "type": "uuid|string|text|integer|decimal|boolean|date|datetime|enum|json|foreign_key", "primary_key": bool, "nullable": bool, "unique": bool, "references": "table.id", "values": [str], "default": any}]}],
  "relationships": [{"from": "table.col", "to": "table.col", "type": "one_to_many|many_to_one|one_to_one|many_to_many", "description": str}],
  "functional_requirements": [{"module": str, "requirement": "The system shall …", "priority": "high|medium|low"}],
  "non_functional_requirements": [{"category": str, "requirement": str}],
  "business_workflows": [{"workflow_name": str, "steps": [str]}],
  "validation_rules": [{"field": str, "rule": str}],
  "notification_rules": [{"event": str, "recipients": [str], "channels": [str]}],
  "reporting_requirements": [{"report_name": str, "filters": [str], "exports": ["PDF","Excel"]}],
  "integration_requirements": [{"name": str, "type": str, "description": str, "required": bool}],
  "security_requirements": [str],
  "risk_priority": [{"area": str, "risk": str, "severity": "High|Medium|Low", "reason": str, "mitigation": str}],
  "ambiguities": [{"area": str, "description": str, "assumption_made": str, "needs_clarification": bool}]
}
Reference foreign keys with the exact 'table.id' form.

THE APPROVED PLAN IS THE BOUNDARY. It is what the customer read and signed off on, and it is the whole of what you are specifying.
- Do NOT introduce a record, role, screen or capability the plan does not contain. Detail and sharpen what is there; never widen it. Anything you add beyond the plan is discarded, so it only costs you attention.
- Give NO minimum counts any thought. Three tables is the right answer when the plan has three records, and one is the right answer when it has one.
- If the plan has no login, the app has no accounts. Do not mention users, roles, permissions, admins, sign-in or audit logs anywhere — not in a requirement, not in a table, not in a risk.
- Each ` + "`table_name`" + ` is the PLAIN PLURAL of the thing it holds: books, students, loans, products, sales. Never pluralise a name that is already plural — ` + "`bookses`" + `, ` + "`studentses`" + `, ` + "`loanses`" + `, ` + "`productses`" + `, ` + "`customerses`" + ` are not words. Nothing downstream corrects this: the builder creates the collection under exactly the name you write, so ` + "`bookses`" + ` ships as a real collection in a real app.`

// enrichment is what the model returns. Only the parts the merge actually uses
// are modelled; the rest is read and discarded, which is the point.
type enrichment struct {
	AppSummary struct {
		BusinessGoal     string `json:"business_goal"`
		ShortDescription string `json:"short_description"`
	} `json:"app_summary"`
	Tables                    []Table          `json:"tables"`
	FunctionalRequirements    []Requirement    `json:"functional_requirements"`
	NonFunctionalRequirements []NonFunctional  `json:"non_functional_requirements"`
	BusinessWorkflows         []enrichWorkflow `json:"business_workflows"`
	ValidationRules           []ValidationRule `json:"validation_rules"`
	NotificationRules         []Notification   `json:"notification_rules"`
	ReportingRequirements     []Reporting      `json:"reporting_requirements"`
	IntegrationRequirements   []Integration    `json:"integration_requirements"`
	RiskPriority              []Risk           `json:"risk_priority"`
	Ambiguities               []Ambiguity      `json:"ambiguities"`
	Branding                  struct {
		LogoImagePrompt string `json:"logo_image_prompt"`
	} `json:"branding"`
}

// enrichWorkflow accepts either key, because the model is asked for
// `workflow_name` and half the time writes `name`.
type enrichWorkflow struct {
	WorkflowName string   `json:"workflow_name"`
	Name         string   `json:"name"`
	Who          string   `json:"who"`
	Steps        []string `json:"steps"`
}

// accountWords are what a specification for an app with no accounts must not
// contain. Finding one means the model widened the plan.
var accountWords = []string{"login", "sign in", "sign-in", "password", "role",
	"permission", "admin", "authenticat"}

// checkEnrichment refuses anything outside the approved plan, in the model's
// own terms so the repair round can act on it.
func checkEnrichment(pack *enrichment, plan *Plan, auth bool) error {
	allowed := map[string]bool{}
	var allowedNames []string
	for _, r := range plan.Records {
		if key := normalizeName(r.Name); key != "" {
			allowed[key] = true
			allowedNames = append(allowedNames, key)
		}
	}
	sortStrings(allowedNames)

	var strays []string
	for _, t := range pack.Tables {
		name := strings.TrimSpace(t.TableName)
		if name == "" {
			return fmt.Errorf("every supplied table needs a table_name")
		}
		if !allowed[normalizeName(name)] && !containsString(strays, name) {
			strays = append(strays, name)
		}
	}
	if len(strays) > 0 {
		sortStrings(strays)
		have := strings.Join(allowedNames, ", ")
		if have == "" {
			have = "(none)"
		}
		return fmt.Errorf("these records are not in the approved plan and must be "+
			"removed: %s. The plan's records are: %s", strings.Join(strays, ", "), have)
	}

	for _, fr := range pack.FunctionalRequirements {
		if strings.TrimSpace(fr.Requirement) == "" {
			return fmt.Errorf("every supplied functional requirement needs requirement text")
		}
	}
	if auth {
		return nil
	}

	var blob strings.Builder
	for _, fr := range pack.FunctionalRequirements {
		blob.WriteString(strings.ToLower(fr.Requirement))
		blob.WriteString(" ")
	}
	var hits []string
	for _, word := range accountWords {
		if strings.Contains(blob.String(), word) {
			hits = append(hits, word)
		}
	}
	if len(hits) > 0 {
		sortStrings(hits)
		return fmt.Errorf("this app has no login, so no requirement may mention %s. "+
			"Remove those requirements", strings.Join(hits, ", "))
	}
	return nil
}

var nonAlnum = regexp.MustCompile(`[^a-z0-9]`)

// normalizeName compares two names loosely enough that "Sale Items" and
// "sale_item" are the same record.
func normalizeName(name string) string {
	key := nonAlnum.ReplaceAllString(strings.ToLower(name), "")
	if strings.HasSuffix(key, "ies") {
		return key[:len(key)-3] + "y"
	}
	if strings.HasSuffix(key, "s") && !strings.HasSuffix(key, "ss") {
		return key[:len(key)-1]
	}
	return key
}

// mergeEnrichment lays the model's content over the composed document without
// letting it grow past the plan.
func mergeEnrichment(doc *Document, pack *enrichment) {
	if v := strings.TrimSpace(pack.AppSummary.BusinessGoal); v != "" {
		doc.AppSummary.BusinessGoal = v
	}
	if v := strings.TrimSpace(pack.AppSummary.ShortDescription); v != "" {
		doc.AppSummary.ShortDescription = v
	}

	// Tables may gain a description and extra columns, never new tables.
	byName := map[string]*Table{}
	for i := range doc.DatabaseDesign.Tables {
		byName[doc.DatabaseDesign.Tables[i].TableName] = &doc.DatabaseDesign.Tables[i]
	}
	for _, t := range pack.Tables {
		name := snakeName(t.TableName)
		target, ok := byName[name]
		if !ok {
			target, ok = byName[tableName(name)]
		}
		if !ok {
			continue
		}
		if v := strings.TrimSpace(t.Description); v != "" {
			target.Description = v
		}
		for _, f := range t.Fields {
			fname := snakeName(f.Name)
			if fname == "" || hasField(target.Fields, fname) {
				continue
			}
			nullable := true
			if f.Nullable != nil {
				nullable = *f.Nullable
			}
			target.Fields = append(target.Fields, Field{
				Name: fname, Type: firstNonEmpty(f.Type, "string"), Nullable: &nullable,
			})
		}
	}

	seen := map[string]bool{}
	for _, r := range doc.FunctionalRequirements {
		seen[strings.ToLower(strings.TrimSpace(r.Requirement))] = true
	}
	for _, fr := range pack.FunctionalRequirements {
		text := sentence(fr.Requirement)
		if text == "" || seen[strings.ToLower(text)] {
			continue
		}
		seen[strings.ToLower(text)] = true
		doc.FunctionalRequirements = append(doc.FunctionalRequirements, Requirement{
			ID:           requirementID("FR", len(doc.FunctionalRequirements)+1),
			Module:       firstNonEmpty(fr.Module, "Core Features"),
			Requirement:  text,
			Priority:     firstNonEmpty(fr.Priority, "medium"),
			AllowedRoles: asList(fr.AllowedRoles),
		})
	}
	for _, nfr := range pack.NonFunctionalRequirements {
		if text := sentence(nfr.Requirement); text != "" {
			doc.NonFunctionalRequirements = append(doc.NonFunctionalRequirements, NonFunctional{
				ID:          requirementID("NFR", len(doc.NonFunctionalRequirements)+1),
				Category:    firstNonEmpty(nfr.Category, "Quality"),
				Requirement: text,
			})
		}
	}

	if len(pack.ValidationRules) > 0 {
		doc.ValidationRules = pack.ValidationRules
	}
	if len(pack.NotificationRules) > 0 {
		doc.NotificationRules = pack.NotificationRules
	}
	if len(pack.ReportingRequirements) > 0 {
		doc.ReportingRequirements = pack.ReportingRequirements
	}
	if len(pack.IntegrationRequirements) > 0 {
		doc.IntegrationRequirements = pack.IntegrationRequirements
	}

	// A half-filled risk is worse than none: it reads as analysis nobody did.
	var risks []Risk
	for _, r := range pack.RiskPriority {
		if r.Area == "" || r.Risk == "" || r.Severity == "" || r.Reason == "" || r.Mitigation == "" {
			continue
		}
		r.ID = requirementID("RISK", len(risks)+1)
		risks = append(risks, r)
	}
	if len(risks) > 0 {
		doc.RiskPriority = risks
	}
	var ambiguities []Ambiguity
	for _, a := range pack.Ambiguities {
		if a.Area == "" || a.Description == "" || a.AssumptionMade == "" {
			continue
		}
		a.ID = requirementID("AMB", len(ambiguities)+1)
		ambiguities = append(ambiguities, a)
	}
	if len(ambiguities) > 0 {
		doc.Ambiguities = ambiguities
	}

	var flows []Workflow
	for _, w := range pack.BusinessWorkflows {
		if len(w.Steps) == 0 {
			continue
		}
		flows = append(flows, Workflow{
			WorkflowName: firstNonEmpty(w.WorkflowName, w.Name, "Workflow"),
			Who:          w.Who, Steps: w.Steps,
		})
	}
	if len(flows) > 0 {
		doc.BusinessWorkflows = flows
	}

	if prompt := strings.TrimSpace(pack.Branding.LogoImagePrompt); prompt != "" {
		if required, _ := doc.Branding["logo_required"].(bool); required {
			doc.Branding["logo_image_prompt"] = prompt
		}
	}

	// The matrix is rebuilt because the requirements it maps have changed.
	doc.Traceability = traceability(doc.FunctionalRequirements, doc.DatabaseDesign.Tables,
		append(append([]Page{}, doc.PublicPages...), doc.ProtectedPages...))
}

// --- the node ------------------------------------------------------------------------

// generateNode composes the specification and then asks the model to sharpen
// it. The composed document is always kept: an unreachable or unhelpful model
// costs detail, never the specification itself.
func (s *Service) generateNode(ctx context.Context, state any) (any, error) {
	st := state.(*State)
	if err := ctx.Err(); err != nil {
		return st, err
	}
	project := st.Project
	if project == nil {
		project = &Project{}
	}
	// The language picker covers the interview and the plan. Everything after
	// approval is an English implementation artifact.
	english := *project
	english.Language = "English"

	plan := st.Plan
	if plan == nil {
		// No plan was ever approved for this project, so the one the interview
		// implies is built and composed exactly as an approved one would be.
		plan = BuildOfflinePlan(&english, st.Session, st.Brief)
		st.Plan = plan
		st.PlanMarkdown = RenderPlanMarkdown(plan, plan.AppName)
	}
	pack := packOf(st.Session)
	auth := AuthOn(pack, plan)

	s.logf(ctx, st.ProjectID, "SrsJsonGeneratorAgent", 10, "Composing the SRS from the approved plan…")
	doc := BuildSRSFromPlan(&english, plan, pack, st.Session, st.Brief)
	if err := doc.Validate(); err != nil {
		s.recordError(ctx, st.ProjectID, "SrsJsonGeneratorAgent",
			fmt.Errorf("composed document failed validation: %w", err))
	}

	s.emit(ctx, st.ProjectID, "SrsJsonGeneratorAgent",
		"Asking the model for domain-specific content…", "info", 35, nil)
	var pack2 enrichment
	err := s.LLM.JSONValid(ctx, roleSRS, enrichSystem, enrichPrompt(st, plan, auth), &pack2,
		func() error { return checkEnrichment(&pack2, plan, auth) })
	if err != nil {
		s.warn(ctx, st.ProjectID, "SrsJsonGeneratorAgent", fmt.Sprintf(
			"Model not used (%s) — keeping the specification composed from the plan.",
			truncate(err.Error(), 140)), 60)
	} else {
		mergeEnrichment(doc, &pack2)
		if err := doc.Validate(); err != nil {
			s.recordError(ctx, st.ProjectID, "SrsJsonGeneratorAgent",
				fmt.Errorf("enriched document failed validation: %w", err))
		}
		s.emit(ctx, st.ProjectID, "SrsJsonGeneratorAgent", fmt.Sprintf(
			"Model enrichment merged: %d tables, %d FR.",
			len(doc.DatabaseDesign.Tables), len(doc.FunctionalRequirements)), "success", 60, nil)
	}

	ApplyInternationalProfile(doc)
	doc.DocumentLanguage = "English"
	doc.ApprovedPlanMarkdown = st.PlanMarkdown

	summary := Summarize(doc)
	note := ""
	if !auth {
		note = ", no login"
	}
	s.emit(ctx, st.ProjectID, "SrsJsonGeneratorAgent", fmt.Sprintf(
		"SRS ready: %d FR, %d NFR, %d tables, %d roles%s.",
		summary.Functional, summary.NonFunctional, summary.Tables, summary.Roles, note),
		"success", 70, map[string]any{"summary": summary})

	st.Document = doc
	return st, nil
}

// enrichPrompt is what the model is shown: the idea, the approved plan as the
// boundary, and the answers behind it.
func enrichPrompt(st *State, plan *Plan, auth bool) string {
	var parts []string
	domain := ""
	if st.Classification != nil {
		domain = firstText(st.Classification["detected_domain"])
	}
	parts = append(parts, "DETECTED DOMAIN: "+firstNonEmpty(domain, "unknown"), "")
	parts = append(parts, "USER IDEA / BRIEF:\n"+truncate(st.Brief, 3000), "")

	if md := strings.TrimSpace(st.PlanMarkdown); md != "" {
		parts = append(parts, "THE APPROVED PLAN — THIS IS THE BOUNDARY:\n"+truncate(md, 6000), "")
	}
	if !auth {
		parts = append(parts, "This app has NO login and NO user accounts. Say nothing "+
			"about users, roles, permissions or admins.", "")
	}
	parts = append(parts, "USER ANSWERS:\n"+answersDigest(st.Session), "")
	parts = append(parts,
		"SRS OUTPUT LANGUAGE (MANDATORY): Write every customer-visible SRS heading, "+
			"description, requirement, workflow step, label and summary in English. "+
			"Preserve machine identifiers in their required format.",
		"Now produce the enrichment JSON described in the system message, tailored "+
			"precisely to this idea.")
	return strings.Join(parts, "\n")
}
