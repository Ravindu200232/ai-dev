package srs

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
)

// Everything between a plan the model just wrote and a plan the customer has
// approved: laying the model's answer over the one it was given, giving every
// role a journey, rendering it for the review page, and the gate that decides
// whether it may be approved at all. Plus the way back — reading a plan out of
// a specification that has since moved on, which is what an edit is applied to.

// --- merge -------------------------------------------------------------------------

// mergePlan lays the model's plan over the skeleton. Anything it left out is
// kept, because a section the model forgot is not a section the customer
// dropped — and on a revision the skeleton is the plan being edited.
func mergePlan(skeleton, plan *Plan) *Plan {
	out := *skeleton
	if v := strings.TrimSpace(plan.AppName); v != "" {
		out.AppName = v
	}
	if v := strings.TrimSpace(plan.ProductIntent); v != "" {
		out.ProductIntent = v
	}
	if v := strings.TrimSpace(plan.LookAndFeel); v != "" {
		out.LookAndFeel = v
	}
	if plan.AccountPolicy != nil {
		out.AccountPolicy = plan.AccountPolicy
	}
	if len(plan.Users) > 0 {
		out.Users = plan.Users
	}
	if len(plan.Screens) > 0 {
		out.Screens = plan.Screens
	}
	if len(plan.Records) > 0 {
		out.Records = plan.Records
	}
	if len(plan.Workflows) > 0 {
		out.Workflows = plan.Workflows
	}
	if len(plan.Features) > 0 {
		out.Features = plan.Features
	}
	if len(plan.Assumptions) > 0 {
		out.Assumptions = plan.Assumptions
	}
	// An empty open-question list is a valid answer — it is what lets the
	// customer approve — so the model's list always replaces the skeleton's.
	if plan.OpenQuestions != nil {
		out.OpenQuestions = plan.OpenQuestions
	}
	out.OpenQuestions = cleanQuestions(out.OpenQuestions)
	out.CustomerNotes = skeleton.CustomerNotes
	out.Workflows = journeysForEveryRole(&out)
	out.normalise()
	return &out
}

// normalise gives every list a value. A section the Studio reads as `null`
// rather than `[]` is one more thing every caller has to guard against, and
// the JSON is a contract with a browser.
func (p *Plan) normalise() {
	if p.Screens == nil {
		p.Screens = []Screen{}
	}
	if p.Users == nil {
		p.Users = []PlanUser{}
	}
	if p.Records == nil {
		p.Records = []PlanRecord{}
	}
	if p.Workflows == nil {
		p.Workflows = []Journey{}
	}
	if p.Features == nil {
		p.Features = []string{}
	}
	if p.Assumptions == nil {
		p.Assumptions = []string{}
	}
	if p.OpenQuestions == nil {
		p.OpenQuestions = []OpenQuestion{}
	}
}

// cleanQuestions drops the blank ones and caps the answers offered at four,
// which is the point past which reading them costs more than they save.
func cleanQuestions(questions []OpenQuestion) []OpenQuestion {
	out := []OpenQuestion{}
	for _, q := range questions {
		text := strings.TrimSpace(q.Question)
		if text == "" {
			continue
		}
		options := []string{}
		for _, opt := range q.Options {
			if opt = strings.TrimSpace(opt); opt != "" && !containsString(options, opt) {
				options = append(options, opt)
			}
		}
		if len(options) > 4 {
			options = options[:4]
		}
		out = append(out, OpenQuestion{Question: text, Required: q.Required, Options: options})
	}
	return out
}

// journeysForEveryRole gives every role a journey. A role with none is a role
// whose half of the app nobody has thought through.
func journeysForEveryRole(plan *Plan) []Journey {
	var roles []string
	for _, u := range plan.Users {
		if role := strings.TrimSpace(u.Role); role != "" {
			roles = append(roles, role)
		}
	}

	flows := []Journey{}
	covered := map[string]bool{}
	for _, w := range plan.Workflows {
		var steps []string
		for _, s := range w.Steps {
			if s = strings.TrimSpace(s); s != "" {
				steps = append(steps, s)
			}
		}
		if len(steps) == 0 {
			continue
		}
		who := strings.TrimSpace(w.Who)
		if who == "" {
			blob := strings.ToLower(w.Name + " " + strings.Join(steps, " "))
			for _, r := range roles {
				if strings.Contains(blob, strings.ToLower(r)) {
					who = r
					break
				}
			}
		}
		name := strings.TrimSpace(w.Name)
		if name == "" {
			name = "Journey"
		}
		flows = append(flows, Journey{Name: name, Who: who, Steps: steps})
		if who != "" {
			covered[strings.ToLower(who)] = true
		}
	}

	needsAccount := plan.AccountPolicy != nil && plan.AccountPolicy.AccountsRequired
	for _, u := range plan.Users {
		role := strings.TrimSpace(u.Role)
		var can []string
		for _, c := range u.CanDo {
			if c = strings.TrimSpace(c); c != "" {
				can = append(can, c)
			}
		}
		if role == "" || covered[strings.ToLower(role)] || len(can) == 0 {
			continue
		}
		steps := can
		if needsAccount {
			steps = append([]string{role + " signs in"}, can...)
		}
		flows = append(flows, Journey{
			Name: "What the " + strings.ToLower(role) + " does here", Who: role, Steps: steps,
		})
		covered[strings.ToLower(role)] = true
	}
	return flows
}

// --- the review page ------------------------------------------------------------------

// RenderPlanMarkdown is the plan as the "full technical plan" panel shows it.
func RenderPlanMarkdown(plan *Plan, appName string) string {
	var out []string
	add := func(lines ...string) { out = append(out, lines...) }

	if appName != "" {
		add("# "+appName, "")
	}
	add(strings.TrimSpace(plan.ProductIntent), "")

	if notes := strings.TrimSpace(plan.CustomerNotes); notes != "" {
		add("## In your own words", "")
		for _, line := range strings.Split(notes, "\n") {
			if strings.TrimSpace(line) != "" {
				add(line)
			}
		}
		add("")
	}

	if len(plan.Users) > 0 {
		add("## Who uses it", "")
		for _, u := range plan.Users {
			role := u.Role
			if role == "" {
				role = "User"
			}
			add("**" + role + "**")
			for _, item := range u.CanDo {
				add("- " + item)
			}
			add("")
		}
	}

	if p := plan.AccountPolicy; p != nil && p.AccountsRequired {
		add("## Account access", "")
		add("- Sign in: " + firstNonEmpty(p.SignInRoute, "required") + " using " + strings.Join(p.SignInFields, ", "))
		if p.RegistrationMode == RegistrationOpen {
			add("- Public sign-up: " + firstNonEmpty(p.SignUpRoute, "required") + " → " + p.RegistrationRole)
		} else {
			add("- Public sign-up: no (" + firstNonEmpty(p.RegistrationMode, "admin-created") + ")")
		}
		add("")
	}

	if len(plan.Screens) > 0 {
		add("## Screens", "", "| Screen | What it is for | Who sees it |", "| --- | --- | --- |")
		for _, s := range plan.Screens {
			who := strings.Join(s.Who, ", ")
			if who == "" {
				who = "Everyone"
			}
			add("| " + s.Name + " | " + s.Purpose + " | " + who + " |")
		}
		add("")
	}

	if len(plan.Records) > 0 {
		add("## What it keeps track of", "")
		for _, r := range plan.Records {
			keeps := strings.Join(r.Keeps, ", ")
			if keeps == "" {
				keeps = "—"
			}
			add("- **" + r.Name + "** — " + keeps)
		}
		add("")
	}

	if len(plan.Workflows) > 0 {
		add("## User journeys", "",
			"How each kind of person moves through the app, start to finish — "+
				"the pages they pass through and what happens on each. Every "+
				"journey here is built to be walkable end to end, and it is what "+
				"the finished app is tested against.", "")
		for _, w := range plan.Workflows {
			name := w.Name
			if name == "" {
				name = "Journey"
			}
			line := "**" + name + "**"
			if who := strings.TrimSpace(w.Who); who != "" {
				line += " — " + who
			}
			add(line)
			for i, step := range w.Steps {
				add(fmt.Sprintf("%d. %s", i+1, step))
			}
			add("")
		}
	}

	if len(plan.Features) > 0 {
		add("## What it does", "")
		for _, f := range plan.Features {
			add("- " + f)
		}
		add("")
	}

	if plan.LookAndFeel != "" {
		add("## Look and feel", "", plan.LookAndFeel, "")
	}

	if len(plan.Assumptions) > 0 {
		add("## Things we assumed", "")
		for _, a := range plan.Assumptions {
			add("- " + a)
		}
		add("")
	}

	if len(plan.OpenQuestions) > 0 {
		add("## Still to settle", "")
		for _, q := range plan.OpenQuestions {
			mark := ""
			if q.Required {
				mark = " **(needed before we can write the SRS)**"
			}
			add("- " + q.Question + mark)
		}
		add("")
	}
	return strings.TrimSpace(strings.Join(out, "\n")) + "\n"
}

// --- the approval gate ----------------------------------------------------------------

// PlanEnvelope is what GET and POST /projects/{id}/plan return. Every field is
// read by studio/components/srs/PlanReview.jsx.
type PlanEnvelope struct {
	Plan          *Plan          `json:"plan"`
	Markdown      string         `json:"markdown"`
	Version       *int           `json:"version"`
	Versions      []int          `json:"versions"`
	ContentHash   string         `json:"content_hash"`
	ChangeRequest string         `json:"change_request"`
	Approved      bool           `json:"approved"`
	ApprovedAt    string         `json:"approved_at"`
	CanApprove    bool           `json:"can_approve"`
	Reason        string         `json:"reason"`
	OpenQuestions []OpenQuestion `json:"open_questions"`
}

// Approval is the answer to POST /projects/{id}/plan/approve.
type Approval struct {
	Approved    bool   `json:"approved"`
	Reason      string `json:"reason"`
	Version     *int   `json:"version"`
	ContentHash string `json:"content_hash,omitempty"`
}

// ContentHash fingerprints the plan itself, so a stored approval can be told
// apart from an approval of a plan that has since moved.
func ContentHash(plan *Plan) string {
	var body any
	raw, err := json.Marshal(plan)
	if err == nil {
		err = json.Unmarshal(raw, &body)
	}
	if err != nil {
		return ""
	}
	// Re-marshalling through a map sorts the keys, which is what makes the
	// fingerprint stable across any later field reordering.
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(body); err != nil {
		return ""
	}
	sum := sha256.Sum256([]byte(strings.TrimRight(buf.String(), "\n")))
	return hex.EncodeToString(sum[:])[:16]
}

// RequiredOpenQuestions are the ones that block approval.
func RequiredOpenQuestions(plan *Plan) []string {
	var out []string
	if plan == nil {
		return out
	}
	for _, q := range plan.OpenQuestions {
		if q.Required && strings.TrimSpace(q.Question) != "" {
			out = append(out, strings.TrimSpace(q.Question))
		}
	}
	return out
}

// verdict is whether this plan may be approved, and if not, why not in words
// the customer can act on.
func verdict(doc *PlanRecordDoc, version *int) (bool, string) {
	if doc == nil {
		return false, "no plan has been generated yet"
	}
	if version != nil && *version != doc.Version {
		return false, "superseded — a newer version of the plan exists"
	}
	if doc.Approved {
		return false, "already approved"
	}
	blocking := RequiredOpenQuestions(&doc.Plan)
	if len(blocking) > 0 {
		more := ""
		if len(blocking) > 1 {
			more = fmt.Sprintf(" (+%d more)", len(blocking)-1)
		}
		return false, "still to settle: " + blocking[0] + more
	}
	return true, ""
}

// PlanState is the latest plan plus whether it can be approved, and why not.
func (s *Service) PlanState(ctx context.Context, projectID string) (*PlanEnvelope, error) {
	doc, err := s.Repo.LatestPlan(ctx, projectID)
	if err != nil {
		return nil, err
	}
	plans, err := s.Repo.ListPlans(ctx, projectID)
	if err != nil {
		return nil, err
	}
	can, reason := verdict(doc, nil)

	env := &PlanEnvelope{
		Versions: []int{}, CanApprove: can, Reason: reason,
		OpenQuestions: []OpenQuestion{},
	}
	for _, p := range plans {
		env.Versions = append(env.Versions, p.Version)
	}
	if doc == nil {
		return env, nil
	}

	// The stored plan is re-normalised on the way out: a plan saved before a
	// role gained a journey should still show that journey now.
	plan := doc.Plan
	plan.Workflows = journeysForEveryRole(&plan)
	plan.normalise()
	name := strings.TrimSpace(plan.AppName)
	if name == "" {
		if project, err := s.Repo.GetProject(ctx, projectID); err == nil && project != nil {
			name = project.Title
		}
	}

	version := doc.Version
	env.Plan, env.Markdown = &plan, RenderPlanMarkdown(&plan, name)
	env.Version, env.ContentHash = &version, doc.ContentHash
	env.ChangeRequest, env.Approved, env.ApprovedAt = doc.ChangeRequest, doc.Approved, doc.ApprovedAt
	if plan.OpenQuestions != nil {
		env.OpenQuestions = plan.OpenQuestions
	}
	return env, nil
}

// ApprovePlan approves the latest plan, or explains why it cannot be.
func (s *Service) ApprovePlan(ctx context.Context, projectID string, version *int) (*Approval, error) {
	doc, err := s.Repo.LatestPlan(ctx, projectID)
	if err != nil {
		return nil, err
	}
	can, reason := verdict(doc, version)
	if !can {
		out := &Approval{Approved: false, Reason: reason}
		if doc != nil {
			v := doc.Version
			out.Version = &v
		}
		return out, nil
	}

	if err := s.Repo.UpdatePlan(ctx, doc.ID, Doc{
		"approved": true, "approved_at": NowISO(), "approved_by": "local-user",
	}); err != nil {
		return nil, err
	}
	if err := s.Repo.UpdateProject(ctx, projectID, Doc{"status": StatusPlanApproved}); err != nil {
		return nil, err
	}
	v := doc.Version
	return &Approval{Approved: true, Version: &v, ContentHash: doc.ContentHash}, nil
}

// ApprovedPlan is the newest approved plan, or nil when nothing is approved.
func (s *Service) ApprovedPlan(ctx context.Context, projectID string) (*PlanRecordDoc, error) {
	plans, err := s.Repo.ListPlans(ctx, projectID)
	if err != nil {
		return nil, err
	}
	for i := len(plans) - 1; i >= 0; i-- {
		if plans[i].Approved {
			return &plans[i], nil
		}
	}
	return nil, nil
}

// --- the plan as it stands now ----------------------------------------------------------

// EffectivePlan is a plan-shaped view of the specification as it stands, which
// is what an edit is applied against. Reading it back from the document rather
// than the approved plan is what lets a customization build on the last one.
func EffectivePlan(doc *Document) *Plan {
	approved := planFromDoc(doc.ApprovedPlan)

	allowed := map[string][]string{}
	for _, m := range doc.RoleAccessMatrix {
		allowed[m.Role] = m.AllowedFunctions
	}
	approvedCan := map[string][]string{}
	for _, u := range approved.Users {
		if u.Role != "" {
			approvedCan[strings.ToLower(u.Role)] = asList(u.CanDo)
		}
	}

	var users []PlanUser
	for _, role := range doc.Roles {
		name := firstNonEmpty(role.RoleName, role.RoleKey)
		if name == "" {
			continue
		}
		can := allowed[role.RoleKey]
		if len(can) == 0 {
			can = allowed[name]
		}
		if len(can) == 0 {
			can = approvedCan[strings.ToLower(name)]
		}
		if len(can) == 0 && role.Description != "" {
			can = asList(strings.Split(role.Description, ";"))
		}
		users = append(users, PlanUser{Role: name, CanDo: can})
	}

	var screens []Screen
	for _, page := range append(append([]Page{}, doc.PublicPages...), doc.ProtectedPages...) {
		if page.PageName == "" {
			continue
		}
		purpose := ""
		if fns := asList(page.Functions); len(fns) > 0 {
			purpose = fns[0]
		}
		who := page.AllowedRoles
		if len(who) == 0 && page.LoginRequired != nil && !*page.LoginRequired {
			who = []string{"Visitor"}
		}
		screens = append(screens, Screen{
			Name: page.PageName, Route: firstNonEmpty(page.Route, "/"),
			Purpose: purpose, Who: who,
		})
	}

	var records []PlanRecord
	for _, table := range doc.DatabaseDesign.Tables {
		if table.TableName == "" {
			continue
		}
		var keeps []string
		for _, f := range table.Fields {
			switch f.Name {
			case "", "id", "created_at", "updated_at":
				continue
			}
			keeps = append(keeps, f.Name)
		}
		records = append(records, PlanRecord{Name: table.TableName, Keeps: keeps})
	}

	var flows []Journey
	for _, w := range doc.BusinessWorkflows {
		if len(w.Steps) == 0 {
			continue
		}
		flows = append(flows, Journey{
			Name: firstNonEmpty(w.WorkflowName, "Workflow"), Who: w.Who, Steps: w.Steps,
		})
	}

	out := &Plan{
		ProductIntent: approved.ProductIntent, LookAndFeel: approved.LookAndFeel,
		Users: users, Screens: screens, Records: records, Workflows: flows,
		Features: asList(approved.Features), AccountPolicy: policyFromAuth(doc, approved),
	}
	if len(out.Users) == 0 {
		out.Users = approved.Users
	}
	if len(out.Screens) == 0 {
		out.Screens = approved.Screens
	}
	if len(out.Records) == 0 {
		out.Records = approved.Records
	}
	if len(out.Workflows) == 0 {
		out.Workflows = approved.Workflows
	}
	return out
}

// policyFromAuth reads the account policy back out of the document, so an edit
// sees the accounts the specification actually has.
func policyFromAuth(doc *Document, approved *Plan) *AccountPolicy {
	if !doc.Auth.LoginRequired {
		return &AccountPolicy{AccountsRequired: false, RegistrationMode: RegistrationNone}
	}
	out := &AccountPolicy{}
	if approved.AccountPolicy != nil {
		copied := *approved.AccountPolicy
		out = &copied
	}
	auth := doc.Auth
	out.AccountsRequired = true
	out.SignInFields, out.RegistrationFields = auth.IdentityFields, auth.RegistrationFields
	out.RegistrationMode = firstNonEmpty(auth.RegistrationMode, RegistrationAdmin)
	out.RegistrationRole, out.ProvisioningRole = auth.RegistrationRole, auth.ProvisioningRole
	out.SignInRoute, out.SignUpRoute = auth.SignInRoute, auth.SignUpRoute
	out.AccountManagementRoute = auth.AccountManagementRoute
	out.InvitationManagementRoute = auth.InvitationManagementRoute
	out.InvitationAcceptRoute = auth.InvitationAcceptRoute
	out.RequestAccessRoute, out.AccessReviewRoute = auth.RequestAccessRoute, auth.AccessReviewRoute
	out.PasswordResetRequired = auth.PasswordResetRequired
	return out
}
