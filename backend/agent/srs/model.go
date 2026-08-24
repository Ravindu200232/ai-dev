// Package srs turns an idea into an approved software requirements
// specification, and hands the result to the builder.
//
// It ran as a Python service on its own port until this port; it is now part of
// the same binary, so the Studio's `/srs/*` calls are served in-process.
package srs

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"strings"
	"sync"
	"time"
)

// The SRS document is the contract with the Studio and with the builder, so its
// field names are pinned by tests. Sections the app never reads are carried
// through as raw JSON rather than modelled, which keeps a model change from
// silently dropping something the customer approved.

// Document is one SRS. Extra is everything the model returned that we do not
// have a field for — it is preserved on read and write.
type Document struct {
	DocumentTitle    string `json:"document_title"`
	ProjectName      string `json:"project_name"`
	Version          string `json:"version"`
	DocumentLanguage string `json:"document_language"`
	SystemCategory   string `json:"system_category"`
	PreparedFor      string `json:"prepared_for"`

	StandardsProfile       map[string]any `json:"standards_profile"`
	DocumentControl        map[string]any `json:"document_control"`
	RequirementsQualityRev map[string]any `json:"requirements_quality_review"`

	AppSummary AppSummary `json:"app_summary"`
	AppType    AppType    `json:"app_type"`
	Auth       Auth       `json:"authentication_requirement"`

	Roles            []Role       `json:"roles"`
	RoleAccessMatrix []RoleAccess `json:"role_access_matrix"`
	PublicPages      []Page       `json:"public_pages"`
	ProtectedPages   []Page       `json:"protected_pages"`
	MainModules      []string     `json:"main_modules"`

	DatabaseDesign DatabaseDesign `json:"database_design"`
	APIDesign      []Endpoint     `json:"api_design"`

	FunctionalRequirements    []Requirement    `json:"functional_requirements"`
	NonFunctionalRequirements []NonFunctional  `json:"non_functional_requirements"`
	BusinessWorkflows         []Workflow       `json:"business_workflows"`
	ValidationRules           []ValidationRule `json:"validation_rules"`
	NotificationRules         []Notification   `json:"notification_rules"`
	SecurityRequirements      []string         `json:"security_requirements"`
	UIUX                      map[string]any   `json:"ui_ux_requirements"`
	ReportingRequirements     []Reporting      `json:"reporting_requirements"`
	IntegrationRequirements   []Integration    `json:"integration_requirements"`

	Assumptions        []string     `json:"assumptions"`
	Constraints        []string     `json:"constraints"`
	Ambiguities        []Ambiguity  `json:"ambiguities"`
	RiskPriority       []Risk       `json:"risk_priority"`
	AcceptanceCriteria []Acceptance `json:"acceptance_criteria"`
	Traceability       []Trace      `json:"requirement_traceability_matrix"`
	Diagrams           []Diagram    `json:"diagrams"`

	Branding             map[string]any   `json:"branding,omitempty"`
	ApprovedPlan         map[string]any   `json:"approved_plan,omitempty"`
	EffectivePlan        map[string]any   `json:"effective_plan,omitempty"`
	ApprovedPlanMarkdown string           `json:"approved_plan_markdown,omitempty"`
	BuilderHandoff       map[string]any   `json:"builder_handoff,omitempty"`
	RevisionHistory      []map[string]any `json:"revision_history,omitempty"`
	ApprovalRecord       []map[string]any `json:"approval_record,omitempty"`
}

type AppSummary struct {
	AppName          string   `json:"app_name"`
	ShortDescription string   `json:"short_description"`
	BusinessGoal     string   `json:"business_goal"`
	TargetUsers      []string `json:"target_users"`
}

type AppType struct {
	PrimaryType    string         `json:"primary_type"`
	SupportedTypes []string       `json:"supported_types"`
	FrontendType   string         `json:"frontend_type,omitempty"`
	BackendType    string         `json:"backend_type,omitempty"`
	DatabaseType   string         `json:"database_type,omitempty"`
	ExampleStack   map[string]any `json:"example_stack"`
	Key            string         `json:"key,omitempty"`
}

type Role struct {
	RoleKey     string `json:"role_key"`
	RoleName    string `json:"role_name"`
	Description string `json:"description"`
}

type RoleAccess struct {
	Role                string   `json:"role"`
	AllowedPages        []string `json:"allowed_pages"`
	RestrictedPages     []string `json:"restricted_pages"`
	AllowedFunctions    []string `json:"allowed_functions"`
	RestrictedFunctions []string `json:"restricted_functions"`
}

type Page struct {
	PageName      string   `json:"page_name"`
	Route         string   `json:"route"`
	PageType      string   `json:"page_type,omitempty"`
	LoginRequired *bool    `json:"login_required,omitempty"`
	AllowedRoles  []string `json:"allowed_roles"`
	Sections      []string `json:"sections"`
	Functions     []string `json:"functions"`
}

type Field struct {
	Name        string `json:"name"`
	Type        string `json:"type"`
	PrimaryKey  bool   `json:"primary_key,omitempty"`
	Nullable    *bool  `json:"nullable,omitempty"`
	Unique      bool   `json:"unique,omitempty"`
	Default     any    `json:"default,omitempty"`
	Values      []any  `json:"values,omitempty"`
	References  string `json:"references,omitempty"`
	Description string `json:"description,omitempty"`
}

type Table struct {
	TableName   string  `json:"table_name"`
	Description string  `json:"description,omitempty"`
	Fields      []Field `json:"fields"`
}

// Relationship uses "from", which is a Go keyword, so the field is named From.
type Relationship struct {
	From        string `json:"from"`
	To          string `json:"to"`
	Type        string `json:"type"`
	Via         string `json:"via,omitempty"`
	Description string `json:"description,omitempty"`
}

type DatabaseDesign struct {
	DataTypeStandards map[string]string `json:"data_type_standards"`
	Tables            []Table           `json:"tables"`
	Relationships     []Relationship    `json:"relationships"`
}

type Endpoint struct {
	Method       string   `json:"method"`
	Path         string   `json:"path"`
	Description  string   `json:"description,omitempty"`
	AuthRequired *bool    `json:"auth_required,omitempty"`
	AllowedRoles []string `json:"allowed_roles"`
}

type Requirement struct {
	ID                 string         `json:"id"`
	Module             string         `json:"module"`
	Requirement        string         `json:"requirement"`
	Priority           string         `json:"priority"`
	AllowedRoles       []string       `json:"allowed_roles"`
	VerificationMethod string         `json:"verification_method,omitempty"`
	Source             string         `json:"source,omitempty"`
	Rationale          string         `json:"rationale,omitempty"`
	QualityReview      *QualityReview `json:"quality_review,omitempty"`
}

// QualityReview is a conservative lint of one requirement's wording. A warning
// is a prompt to look, never a claim that the requirement is wrong.
type QualityReview struct {
	Status   string   `json:"status"`
	Warnings []string `json:"warnings"`
}

type NonFunctional struct {
	ID                 string         `json:"id"`
	Category           string         `json:"category"`
	Requirement        string         `json:"requirement"`
	VerificationMethod string         `json:"verification_method,omitempty"`
	Source             string         `json:"source,omitempty"`
	QualityReview      *QualityReview `json:"quality_review,omitempty"`
}

type Workflow struct {
	WorkflowName string   `json:"workflow_name"`
	Who          string   `json:"who,omitempty"`
	Steps        []string `json:"steps"`
}

type ValidationRule struct {
	Field string `json:"field"`
	Rule  string `json:"rule"`
}

type Notification struct {
	Event      string   `json:"event"`
	Recipients []string `json:"recipients"`
	Channels   []string `json:"channels"`
}

type Reporting struct {
	ReportName string   `json:"report_name"`
	Filters    []string `json:"filters"`
	Exports    []string `json:"exports"`
}

type Integration struct {
	Name        string `json:"name"`
	Type        string `json:"type"`
	Description string `json:"description"`
	Required    *bool  `json:"required,omitempty"`
}

type Ambiguity struct {
	ID                 string `json:"id"`
	Area               string `json:"area"`
	Description        string `json:"description"`
	AssumptionMade     string `json:"assumption_made"`
	NeedsClarification bool   `json:"needs_clarification"`
}

type Risk struct {
	ID         string `json:"id"`
	Area       string `json:"area"`
	Risk       string `json:"risk"`
	Severity   string `json:"severity"`
	Reason     string `json:"reason"`
	Mitigation string `json:"mitigation"`
}

type Acceptance struct {
	ID        string `json:"id,omitempty"`
	Criterion string `json:"criterion"`
}

type Trace struct {
	RequirementID      string   `json:"requirement_id"`
	Module             string   `json:"module"`
	Pages              []string `json:"pages"`
	Tables             []string `json:"tables"`
	TestCase           string   `json:"test_case,omitempty"`
	Source             string   `json:"source,omitempty"`
	VerificationMethod string   `json:"verification_method,omitempty"`
}

// Auth is what the specification decided about accounts. Its defaults are the
// "no login at all" case, because most small apps do not need one.
type Auth struct {
	LoginRequired             bool     `json:"login_required"`
	SignInRoute               string   `json:"sign_in_route,omitempty"`
	IdentityFields            []string `json:"identity_fields"`
	RegistrationFields        []string `json:"registration_fields"`
	RegistrationMode          string   `json:"registration_mode"`
	SelfRegistration          bool     `json:"self_registration"`
	SignUpRoute               string   `json:"sign_up_route,omitempty"`
	RegistrationRole          string   `json:"registration_role,omitempty"`
	RegistrationRoles         []string `json:"registration_roles"`
	ProvisioningRole          string   `json:"provisioning_role,omitempty"`
	AccountManagementRoute    string   `json:"account_management_route,omitempty"`
	InvitationManagementRoute string   `json:"invitation_management_route,omitempty"`
	InvitationAcceptRoute     string   `json:"invitation_accept_route,omitempty"`
	RequestAccessRoute        string   `json:"request_access_route,omitempty"`
	AccessReviewRoute         string   `json:"access_review_route,omitempty"`
	PasswordResetRequired     bool     `json:"password_reset_required"`
	SignedOutProtectedAccess  string   `json:"signed_out_protected_access,omitempty"`
	WrongRoleAccess           string   `json:"wrong_role_access,omitempty"`
	AuthTransport             string   `json:"auth_transport"`
}

// Diagram is one rendered artifact. The Studio reads `kind`, `source`, `svg`,
// `svg_path` and `png_path`.
type Diagram struct {
	ID                 string `json:"id"`
	Kind               string `json:"kind"`
	Title              string `json:"title"`
	Format             string `json:"format"`
	Source             string `json:"source"`
	Standard           string `json:"standard,omitempty"`
	Applicable         bool   `json:"applicable"`
	ApplicabilityNote  string `json:"applicability_note,omitempty"`
	CanonicalRendering string `json:"canonical_rendering,omitempty"`
	GeneratedBy        string `json:"generated_by,omitempty"`
	RenderedBy         string `json:"rendered_by,omitempty"`
	MmdPath            string `json:"mmd_path,omitempty"`
	SvgPath            string `json:"svg_path,omitempty"`
	PngPath            string `json:"png_path,omitempty"`
	SVG                string `json:"svg,omitempty"` // inlined on read when small enough
}

// Envelope is how a document is stored and returned: the Studio unwraps
// `srs_document`, and falls back to the object itself when the key is absent.
type Envelope struct {
	Document Document `json:"srs_document"`
}

// --- project ------------------------------------------------------------------

// Project is one idea being worked through the interview and into an SRS.
type Project struct {
	ID                  string         `json:"id"`
	Title               string         `json:"title"`
	RawIdea             string         `json:"raw_idea"`
	DetectedDomain      string         `json:"detected_domain"`
	DomainKey           string         `json:"domain_key"`
	Status              string         `json:"status"`
	CurrentVersion      string         `json:"current_version"`
	Language            string         `json:"language"`
	Classification      map[string]any `json:"classification,omitempty"`
	Complexity          map[string]any `json:"complexity,omitempty"`
	SuggestedStack      map[string]any `json:"suggested_stack,omitempty"`
	Coverage            map[string]any `json:"coverage,omitempty"`
	CoverageScore       float64        `json:"coverage_score"`
	NeedsClarification  bool           `json:"needs_clarification"`
	ClarificationReason string         `json:"clarification_reason,omitempty"`
	CreatedAt           string         `json:"created_at"`
	UpdatedAt           string         `json:"updated_at"`
}

// The statuses a project moves through, in order.
const (
	StatusIntake       = "intake"
	StatusAnalyzing    = "analyzing"
	StatusQuestioning  = "questioning"
	StatusPlanning     = "planning"
	StatusPlanApproved = "plan_approved"
	StatusGenerated    = "generated"
	StatusApproved     = "approved"
	StatusCustomized   = "customized"
)

// NewProject starts one at the intake stage.
func NewProject(idea, language string) Project {
	now := NowISO()
	if language == "" {
		language = "English"
	}
	return Project{
		ID: NewID("prj_"), Title: TitleFrom(idea), RawIdea: strings.TrimSpace(idea),
		DetectedDomain: "Custom", DomainKey: "custom",
		Status: StatusIntake, CurrentVersion: "0.0.0", Language: language,
		CreatedAt: now, UpdatedAt: now,
	}
}

// TitleFrom names a project from the first line of the idea.
func TitleFrom(idea string) string {
	title := strings.TrimSpace(idea)
	if i := strings.IndexAny(title, ".\n"); i > 0 {
		title = title[:i]
	}
	title = strings.TrimSpace(title)
	if len(title) > 80 {
		title = strings.TrimSpace(title[:80])
	}
	if title == "" {
		return "Untitled project"
	}
	return title
}

// --- plan ---------------------------------------------------------------------

// Plan is the customer-facing restatement of the idea, approved before any SRS
// is written. Its fields are read by studio/components/srs/PlanReview.jsx.
type Plan struct {
	AppName       string         `json:"app_name"`
	ProductIntent string         `json:"product_intent"`
	CustomerNotes string         `json:"customer_notes"`
	LookAndFeel   string         `json:"look_and_feel"`
	Screens       []Screen       `json:"screens"`
	Users         []PlanUser     `json:"users"`
	Records       []PlanRecord   `json:"records"`
	Workflows     []Journey      `json:"workflows"`
	Features      []string       `json:"features"`
	AccountPolicy *AccountPolicy `json:"account_policy"`
	Assumptions   []string       `json:"assumptions"`
	OpenQuestions []OpenQuestion `json:"open_questions"`
}

// Journey is one whole job, start to finish. The plan calls it `name` where
// the specification's own workflows use `workflow_name`, and PlanReview.jsx
// reads `name` — so the two shapes stay separate types.
type Journey struct {
	Name  string   `json:"name"`
	Who   string   `json:"who,omitempty"`
	Steps []string `json:"steps"`
}

// AccountPolicy is who may hold an account and how they come to have one. It
// is decided once, in the plan, and the SRS derives its sign-in pages, its
// account requirements and its API from it rather than re-deciding.
type AccountPolicy struct {
	AccountsRequired   bool     `json:"accounts_required"`
	SignInFields       []string `json:"sign_in_fields,omitempty"`
	RegistrationFields []string `json:"registration_fields,omitempty"`
	RegistrationMode   string   `json:"registration_mode"`
	RegistrationRole   string   `json:"registration_role,omitempty"`
	ProvisioningRole   string   `json:"provisioning_role,omitempty"`

	SignInRoute string `json:"sign_in_route,omitempty"`
	SignUpRoute string `json:"sign_up_route,omitempty"`

	AccountManagementRoute    string `json:"account_management_route,omitempty"`
	InvitationManagementRoute string `json:"invitation_management_route,omitempty"`
	InvitationAcceptRoute     string `json:"invitation_accept_route,omitempty"`
	RequestAccessRoute        string `json:"request_access_route,omitempty"`
	AccessReviewRoute         string `json:"access_review_route,omitempty"`

	PasswordResetRequired bool `json:"password_reset_required"`
}

// The ways an account can come to exist.
const (
	RegistrationNone    = "none"
	RegistrationOpen    = "open"
	RegistrationAdmin   = "admin_created"
	RegistrationInvite  = "invite"
	RegistrationRequest = "request"
)

type Screen struct {
	Name    string   `json:"name"`
	Route   string   `json:"route"`
	Purpose string   `json:"purpose"`
	Who     []string `json:"who"`
}

type PlanUser struct {
	Role  string   `json:"role"`
	CanDo []string `json:"can_do"`
}

type PlanRecord struct {
	Name  string   `json:"name"`
	Keeps []string `json:"keeps"`
}

type OpenQuestion struct {
	Question string   `json:"question"`
	Required bool     `json:"required"`
	Options  []string `json:"options"`
}

// PlanRecordDoc is one stored, versioned plan.
type PlanRecordDoc struct {
	ID            string `json:"id"`
	ProjectID     string `json:"project_id"`
	Version       int    `json:"version"`
	Plan          Plan   `json:"plan"`
	Markdown      string `json:"markdown"`
	ContentHash   string `json:"content_hash"`
	RevisionOf    int    `json:"revision_of,omitempty"`
	ChangeRequest string `json:"change_request,omitempty"`
	Approved      bool   `json:"approved"`
	ApprovedAt    string `json:"approved_at,omitempty"`
	ApprovedBy    string `json:"approved_by,omitempty"`
	CreatedAt     string `json:"created_at"`
}

// --- interview ----------------------------------------------------------------

// Question is what the Studio renders. It carries both the current field names
// and the older ones the UI still reads, because Interview.jsx checks several.
type Question struct {
	ID               string   `json:"id"`
	Question         string   `json:"question"`
	WhyNeeded        string   `json:"why_needed"`
	AnswerType       string   `json:"answer_type"`
	SuggestedOptions []string `json:"suggested_options"`
	MapsToSRSFields  []string `json:"maps_to_srs_fields"`
	CoverageAreas    []string `json:"coverage_areas"`
	Required         bool     `json:"required"`

	Key            string   `json:"key"`
	Topic          string   `json:"topic"`
	Subject        string   `json:"subject"`
	Kind           string   `json:"kind"`
	Index          int      `json:"index"`
	Total          int      `json:"total"`
	Options        []Option `json:"options"`
	Recommended    any      `json:"recommended,omitempty"`
	Placeholder    string   `json:"placeholder"`
	Optional       bool     `json:"optional"`
	Multiline      bool     `json:"multiline"`
	Prefill        []string `json:"prefill"`
	PrefillNote    string   `json:"prefill_note,omitempty"`
	OutputLanguage string   `json:"output_language,omitempty"`
}

// Option is one choice. `suggested` marks the one the agent recommends.
// Value is untyped because a yes/no topic offers booleans while every other
// topic offers strings, and the Studio hands whichever it was given straight
// back as the answer.
type Option struct {
	Label     string `json:"label"`
	Value     any    `json:"value"`
	Suggested bool   `json:"suggested,omitempty"`
	Hint      string `json:"hint,omitempty"`
	Icon      string `json:"icon,omitempty"`
}

// OptionValue renders an option's value as the string the rest of the service
// compares against.
func (o Option) OptionValue() string { return fmt.Sprint(o.Value) }

// Answer is one recorded turn.
type Answer struct {
	QuestionID  string   `json:"question_id"`
	Value       any      `json:"value"`
	RawText     string   `json:"raw_text,omitempty"`
	Attachments []string `json:"attachments,omitempty"`
}

// Session is the whole interview for one project.
type Session struct {
	ID        string `json:"id"`
	ProjectID string `json:"project_id"`
	Mode      string `json:"mode"`
	RawIdea   string `json:"raw_idea"`
	Language  string `json:"language"`

	GuessedAppType           string  `json:"guessed_app_type"`
	GuessedAppTypeConfidence float64 `json:"guessed_app_type_confidence"`
	GuessedAppTypeWhy        string  `json:"guessed_app_type_why"`
	AppType                  string  `json:"app_type"`
	Pack                     *Pack   `json:"pack"`

	Answers        map[string]AnswerEntry `json:"answers"`
	Clarifications map[string]any         `json:"clarifications,omitempty"`
	Asked          []string               `json:"asked"`
	Questions      []Question             `json:"questions"`
	Current        int                    `json:"current"`
	Total          int                    `json:"total"`

	CoverageScore float64        `json:"coverage_score"`
	Coverage      map[string]any `json:"coverage"`
	Complete      bool           `json:"complete"`
	CreatedAt     string         `json:"created_at"`
}

// AnswerEntry is one answer as the session stores it.
type AnswerEntry struct {
	Value       any      `json:"value"`
	Text        string   `json:"text,omitempty"`
	Attachments []string `json:"attachments,omitempty"`
	At          string   `json:"at"`
}

// --- shared helpers -------------------------------------------------------------

// NowISO is the timestamp format every stored document uses, and every sort key
// depends on it staying lexicographically ordered.
// isoLayout is fixed width, unlike RFC3339Nano, which drops trailing zeros.
// Records are ordered by comparing these strings, and variable-width fractions
// do not compare in the order of the instants they name: ".1Z" sorts after
// ".1000001Z" even though it names the earlier one.
const isoLayout = "2006-01-02T15:04:05.000000000Z07:00"

var (
	isoMu   sync.Mutex
	isoLast time.Time
)

// NowISO is the timestamp every stored record carries. It never repeats and
// never goes backwards: a Windows clock moves in steps coarse enough that two
// saves in a row otherwise share a timestamp, which leaves "the latest" of them
// down to whatever order the store happens to return.
func NowISO() string {
	isoMu.Lock()
	defer isoMu.Unlock()
	now := time.Now().UTC()
	if !now.After(isoLast) {
		now = isoLast.Add(time.Nanosecond)
	}
	isoLast = now
	return now.Format(isoLayout)
}

// NewID matches the Python id format: a prefix plus 24 hex characters.
func NewID(prefix string) string {
	var raw [12]byte
	if _, err := rand.Read(raw[:]); err != nil {
		// A clock-based id is still unique enough to store; ids are never secrets.
		return prefix + hex.EncodeToString([]byte(time.Now().UTC().Format(time.RFC3339Nano)))[:24]
	}
	return prefix + hex.EncodeToString(raw[:])
}

// Summary is the inspector count block the Studio renders under the SRS.
type Summary struct {
	Functional      int `json:"functional"`
	NonFunctional   int `json:"non_functional"`
	UseCases        int `json:"use_cases"`
	OpenAmbiguities int `json:"open_ambiguities"`
	Tables          int `json:"tables"`
	Roles           int `json:"roles"`
	Modules         int `json:"modules"`
	Diagrams        int `json:"diagrams"`
}

// Summarize counts what the document holds.
func Summarize(doc *Document) Summary {
	open := 0
	for _, a := range doc.Ambiguities {
		if a.NeedsClarification {
			open++
		}
	}
	return Summary{
		Functional:      len(doc.FunctionalRequirements),
		NonFunctional:   len(doc.NonFunctionalRequirements),
		UseCases:        len(doc.BusinessWorkflows),
		OpenAmbiguities: open,
		Tables:          len(doc.DatabaseDesign.Tables),
		Roles:           len(doc.Roles),
		Modules:         len(doc.MainModules),
		Diagrams:        len(doc.Diagrams),
	}
}

// Validate enforces what the Python pydantic validators enforced. A document
// that fails this is not written.
func (d *Document) Validate() error {
	switch {
	case len(d.FunctionalRequirements) < 3:
		return &ValidationError{"functional_requirements must contain at least 3 entries"}
	case len(d.NonFunctionalRequirements) < 3:
		return &ValidationError{"non_functional_requirements must contain at least 3 entries"}
	case len(d.Roles) < 1:
		return &ValidationError{"roles must contain at least 1 entry"}
	case strings.TrimSpace(d.ProjectName) == "":
		return &ValidationError{"project_name is required"}
	}
	return nil
}

// ValidationError is a specification that does not meet the minimum shape.
type ValidationError struct{ Reason string }

func (e *ValidationError) Error() string { return e.Reason }
