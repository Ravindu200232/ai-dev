package srs

import (
	_ "embed"
	"encoding/json"
	"regexp"
	"strings"
	"sync"
)

// The catalogs below were 1,655 lines of Python that held no logic. They are
// data, so they are embedded as data and read once.

//go:embed data/domains.json
var domainsJSON []byte

//go:embed data/app_types.json
var appTypesJSON []byte

//go:embed data/topics.json
var topicsJSON []byte

//go:embed data/coverage.json
var coverageJSON []byte

//go:embed data/standards.json
var standardsJSON []byte

// GenericDomain is the fallback when nothing matches.
const GenericDomain = "custom"

// Domain is one industry template: the roles, modules and tables a hotel or a
// hospital starts from before the customer says anything specific.
type Domain struct {
	Label          string        `json:"label"`
	SystemCategory string        `json:"system_category"`
	AppTypePrimary string        `json:"app_type_primary"`
	Keywords       []string      `json:"keywords"`
	Roles          []Role        `json:"roles"`
	Modules        []string      `json:"modules"`
	Tables         []Table       `json:"tables"`
	Workflows      []Workflow    `json:"workflows"`
	PublicPages    []DomainPage  `json:"public_pages"`
	ProtectedPages []DomainPage  `json:"protected_pages"`
	Integrations   []Integration `json:"integrations"`
	FeatureOptions []string      `json:"feature_options"`
	Extra          map[string]any
}

// DomainPage is one page a trade's apps normally have. Public pages carry
// sections; protected ones carry the roles allowed in and what they may do.
type DomainPage struct {
	PageName     string   `json:"page_name"`
	Route        string   `json:"route"`
	PageType     string   `json:"page_type,omitempty"`
	Sections     []string `json:"sections,omitempty"`
	AllowedRoles []string `json:"allowed_roles,omitempty"`
	Functions    []string `json:"functions,omitempty"`
}

// AppTypeDef is one structural archetype — a POS, a dashboard, a landing page.
type AppTypeDef struct {
	Key         string   `json:"key"`
	Label       string   `json:"label"`
	Icon        string   `json:"icon"`
	Desc        string   `json:"desc"`
	Archetype   string   `json:"archetype"`
	QuestionSet string   `json:"question_set"`
	Keywords    []string `json:"keywords"`
	Shell       Shell    `json:"shell"`
	Palette     string   `json:"palette"`
	AuthDefault bool     `json:"auth_default"`
	AuthPolicy  string   `json:"auth_policy"`

	DefaultRoles    []string    `json:"default_roles"`
	DefaultPages    []PackPage  `json:"default_pages"`
	DefaultEntities []string    `json:"default_entities"`
	Features        []string    `json:"features"`
	Operations      []Operation `json:"operations"`

	RequiredPages       []string `json:"required_pages"`
	OptionalPages       []string `json:"optional_pages"`
	ProhibitedPageTypes []string `json:"prohibited_page_types"`
	BaseOperations      []string `json:"base_operations"`
}

// Shell is the frame an app type fixes: whether it has a public site, a
// sidebar and a navbar. No later stage may overwrite it.
type Shell struct {
	PublicSite bool `json:"public_site"`
	Sidebar    bool `json:"sidebar"`
	Navbar     bool `json:"navbar"`
}

// PackPage is one page an app type ships with.
type PackPage struct {
	Name     string   `json:"name"`
	Route    string   `json:"route"`
	Type     string   `json:"type"`
	Sections []string `json:"sections,omitempty"`
}

// Operation is one named thing the app does, with the route behind it.
type Operation struct {
	ID      string   `json:"id"`
	Name    string   `json:"name"`
	Method  string   `json:"method"`
	Path    string   `json:"path"`
	Effects []string `json:"effects,omitempty"`
}

// TopicDef is one interview question template. The three callable fields are
// stored as the source expression they had in Python and resolved through the
// registry in interview.go — there are only nineteen distinct ones.
type TopicDef struct {
	Key             string   `json:"key"`
	Kind            string   `json:"kind"`
	Label           string   `json:"label"`
	Intent          string   `json:"intent"`
	Placeholder     string   `json:"placeholder"`
	Fixed           string   `json:"fixed"`
	Optional        bool     `json:"optional"`
	Multiline       bool     `json:"multiline"`
	OptionsLocked   bool     `json:"options_locked"`
	Profiles        []string `json:"profiles"`
	SRSFields       []string `json:"srs_fields"`
	Coverage        []string `json:"coverage"`
	FallbackOptions []Option `json:"fallback_options"`

	AppliesTo   string `json:"applies_to"`
	OptionsFrom string `json:"options_from"`
	RepeatsOver string `json:"repeats_over"`
}

// Standards is the ISO/IEC/IEEE 29148 profile the document declares.
type Standards struct {
	SRSStandard      string              `json:"srs_standard"`
	SRSStandardTitle string              `json:"srs_standard_title"`
	UMLStandard      string              `json:"uml_standard"`
	BPMNStandard     string              `json:"bpmn_standard"`
	ERDNotation      string              `json:"erd_notation"`
	DFDNotation      string              `json:"dfd_notation"`
	DiagramNotation  map[string][]string `json:"diagram_notation"`
}

// knowledge is everything loaded from the embedded files, parsed once.
type knowledge struct {
	Domains     map[string]Domain
	AppTypes    map[string]AppTypeDef
	Palettes    map[string]map[string]string
	Topics      []TopicDef
	Signals     map[string][]string
	Business    []string
	Standards   Standards
	GuessFloor  float64
	Placeholder map[string]bool
}

var (
	once   sync.Once
	loaded knowledge
)

// Knowledge parses the embedded catalogs on first use.
func Knowledge() *knowledge {
	once.Do(func() {
		loaded.Domains = map[string]Domain{}
		var rawDomains map[string]map[string]any
		if err := json.Unmarshal(domainsJSON, &rawDomains); err == nil {
			for key, body := range rawDomains {
				var d Domain
				if raw, err := json.Marshal(body); err == nil {
					_ = json.Unmarshal(raw, &d)
				}
				d.Extra = body
				loaded.Domains[key] = d
			}
		}

		var app struct {
			AppTypes    map[string]AppTypeDef        `json:"app_types"`
			Palettes    map[string]map[string]string `json:"palettes"`
			GuessFloor  float64                      `json:"guess_floor"`
			Placeholder []string                     `json:"placeholder_entities"`
		}
		loaded.Placeholder = map[string]bool{}
		if err := json.Unmarshal(appTypesJSON, &app); err == nil {
			loaded.AppTypes, loaded.Palettes = app.AppTypes, app.Palettes
			loaded.GuessFloor = app.GuessFloor
			for _, name := range app.Placeholder {
				loaded.Placeholder[name] = true
			}
		}
		if loaded.GuessFloor == 0 {
			loaded.GuessFloor = 0.6
		}

		var topics struct {
			Topics []TopicDef `json:"topics"`
		}
		if err := json.Unmarshal(topicsJSON, &topics); err == nil {
			loaded.Topics = topics.Topics
		}

		var coverage struct {
			Signals            map[string][]string `json:"signals"`
			BusinessIndicators []string            `json:"business_indicators"`
		}
		if err := json.Unmarshal(coverageJSON, &coverage); err == nil {
			loaded.Signals, loaded.Business = coverage.Signals, coverage.BusinessIndicators
		}

		_ = json.Unmarshal(standardsJSON, &loaded.Standards)
	})
	return &loaded
}

// GetDomain returns a template, falling back to the generic one.
func GetDomain(key string) Domain {
	k := Knowledge()
	if d, ok := k.Domains[strings.ToLower(strings.TrimSpace(key))]; ok {
		return d
	}
	return k.Domains[GenericDomain]
}

// wordPattern splits text the way the Python classifier did.
var wordPattern = regexp.MustCompile(`[a-z0-9]+`)

// ClassifyDomain scores the idea against every domain's keywords. A single-word
// keyword is worth one point and a phrase two, because a phrase match is much
// less likely to be a coincidence.
func ClassifyDomain(text string) (key string, confidence float64) {
	k := Knowledge()
	lower := strings.ToLower(text)
	tokens := map[string]bool{}
	for _, w := range wordPattern.FindAllString(lower, -1) {
		tokens[w] = true
	}

	best, bestKey, total := 0, GenericDomain, 0
	for name, domain := range k.Domains {
		if name == GenericDomain {
			continue
		}
		score := 0
		for _, keyword := range domain.Keywords {
			keyword = strings.ToLower(strings.TrimSpace(keyword))
			if keyword == "" {
				continue
			}
			if strings.Contains(keyword, " ") {
				if strings.Contains(lower, keyword) {
					score += 2
				}
				continue
			}
			if tokens[keyword] {
				score++
			}
		}
		total += score
		if score > best {
			best, bestKey = score, name
		}
	}
	if best == 0 {
		return GenericDomain, 0.35
	}
	share := float64(best) / float64(max(total, 1))
	bonus := float64(min(best, 4)) * 0.05
	return bestKey, minFloat(0.97, 0.45+0.5*share+bonus)
}

// GuessAppType scores the idea against each app type's keywords. Below the
// floor the guess is not confident enough to skip asking.
func GuessAppType(text string) (key string, confidence float64, why string) {
	k := Knowledge()
	lower := strings.ToLower(text)
	tokens := map[string]bool{}
	for _, w := range wordPattern.FindAllString(lower, -1) {
		tokens[w] = true
	}

	best, bestKey, total := 0.0, "other", 0.0
	var hits []string
	for name, def := range k.AppTypes {
		score := 0.0
		var matched []string
		for _, keyword := range def.Keywords {
			keyword = strings.ToLower(strings.TrimSpace(keyword))
			if keyword == "" {
				continue
			}
			if strings.Contains(keyword, " ") {
				if strings.Contains(lower, keyword) {
					score += 2
					matched = append(matched, keyword)
				}
				continue
			}
			if tokens[keyword] {
				score++
				matched = append(matched, keyword)
			}
		}
		total += score
		if score > best {
			best, bestKey, hits = score, name, matched
		}
	}
	if best == 0 {
		return "other", 0, ""
	}
	confidence = minFloat(0.97, 0.45+0.5*best/maxFloat(total, 1)+minFloat(best, 4)*0.05)
	return bestKey, confidence, "matched " + strings.Join(hits, ", ")
}

// AppTypeFor returns one archetype definition, falling back to "other".
func AppTypeFor(key string) AppTypeDef {
	k := Knowledge()
	if def, ok := k.AppTypes[strings.ToLower(strings.TrimSpace(key))]; ok {
		return def
	}
	return k.AppTypes["other"]
}

// AppTypeOptions is the picker the first interview question offers.
func AppTypeOptions() []Option {
	k := Knowledge()
	order := []string{"pos", "saas", "ecommerce", "dashboard", "blog", "landing", "portfolio", "utility", "other"}
	out := make([]Option, 0, len(order))
	for _, key := range order {
		if def, ok := k.AppTypes[key]; ok {
			out = append(out, Option{Label: def.Label, Value: key, Hint: def.Desc, Icon: def.Icon})
		}
	}
	return out
}

// CoverageCovered reports whether the brief says anything about one area.
func CoverageCovered(area, brief string) bool {
	lower := strings.ToLower(brief)
	for _, signal := range Knowledge().Signals[area] {
		if strings.Contains(lower, strings.ToLower(signal)) {
			return true
		}
	}
	return false
}

// CoverageAreas is the fixed list the auditor scores against, in order.
var CoverageAreas = []string{
	"business_type", "main_goal", "users_roles", "auth", "public_pages",
	"app_surfaces", "features_modules", "data_entities", "payments_billing",
	"reports", "notifications", "file_uploads", "integrations",
	"security_privacy", "performance", "devices_mobile", "languages",
	"deployment_stack", "special_rules",
}

// CriticalAreas are the ones a specification cannot be written without.
var CriticalAreas = map[string]bool{
	"business_type": true, "main_goal": true, "users_roles": true,
	"features_modules": true, "data_entities": true,
}

func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

// --- the pack -------------------------------------------------------------------
//
// A pack is the structural app type merged with the business domain the idea
// reads as. It is settled once, when the interview picks the app type, and
// every later stage reads it rather than re-guessing: the interview offers its
// entities and roles as options, the planner draws its screens from its pages,
// and the composer derives tables from its domain tables.

// Pack is that merge. Its field names are the Python dictionary's keys, because
// the session stores it and older sessions have to keep loading.
type Pack struct {
	AppType     string `json:"app_type"`
	AppLabel    string `json:"app_label"`
	Archetype   string `json:"archetype"`
	QuestionSet string `json:"question_set"`
	AuthPolicy  string `json:"auth_policy"`
	AuthDefault bool   `json:"auth_default"`

	RequiredPages       []string `json:"required_pages"`
	OptionalPages       []string `json:"optional_pages"`
	ProhibitedPageTypes []string `json:"prohibited_page_types"`
	BaseOperations      []string `json:"base_operations"`

	Shell       Shell             `json:"shell"`
	Palette     map[string]string `json:"palette"`
	PaletteName string            `json:"palette_name"`

	Roles      []string    `json:"roles"`
	Entities   []string    `json:"entities"`
	Features   []string    `json:"features"`
	Pages      []PackPage  `json:"pages"`
	Operations []Operation `json:"operations"`

	Domain           string  `json:"domain"`
	DomainLabel      string  `json:"domain_label"`
	DomainConfidence float64 `json:"domain_confidence"`

	DomainModules        []string      `json:"domain_modules"`
	DomainTables         []Table       `json:"domain_tables"`
	DomainWorkflows      []Workflow    `json:"domain_workflows"`
	DomainPublicPages    []DomainPage  `json:"domain_public_pages"`
	DomainProtectedPages []DomainPage  `json:"domain_protected_pages"`
	DomainIntegrations   []Integration `json:"domain_integrations"`
}

// DomainFloor is the confidence at which the domain library is trusted enough
// to shape roles, records and features rather than only colour the wording.
const DomainFloor = 0.6

// BuildPack merges the chosen app type with the domain the idea reads as.
func BuildPack(appTypeKey, idea string) *Pack {
	k := Knowledge()
	app := AppTypeFor(appTypeKey)
	domainKey, confidence := ClassifyDomain(idea)
	domain := GetDomain(domainKey)
	confident := confidence >= DomainFloor

	// A confident domain names the people; otherwise the app type does.
	roles := append([]string{}, app.DefaultRoles...)
	if confident && app.AuthDefault {
		roles = nil
		for _, r := range domain.Roles {
			if r.RoleKey != "guest" && r.RoleKey != "super_admin" {
				roles = append(roles, r.RoleKey)
			}
		}
	}

	// The trade's own records come first; the app type's generic ones fill in,
	// minus the placeholders that mean "we had to put something here".
	entities := append([]string{}, app.DefaultEntities...)
	if confident {
		entities = nil
		for _, t := range domain.Tables {
			if name := singularPascal(t.TableName); name != "" && !containsString(entities, name) {
				entities = append(entities, name)
			}
		}
		for _, name := range app.DefaultEntities {
			if !containsString(entities, name) && !k.Placeholder[name] {
				entities = append(entities, name)
			}
		}
	}
	if app.Archetype == archetypeLanding || app.Archetype == archetypeTool {
		if len(entities) > 1 {
			entities = entities[:1]
		}
	}

	features := append([]string{}, app.Features...)
	if confident {
		for _, feat := range domain.FeatureOptions {
			if !containsString(features, feat) {
				features = append(features, feat)
			}
		}
	}

	key := strings.ToLower(strings.TrimSpace(appTypeKey))
	if _, ok := k.AppTypes[key]; !ok {
		key = DefaultAppType
	}

	return &Pack{
		AppType: key, AppLabel: app.Label, Archetype: app.Archetype,
		QuestionSet: app.QuestionSet, AuthPolicy: app.AuthPolicy,
		AuthDefault:   app.AuthDefault,
		RequiredPages: app.RequiredPages, OptionalPages: app.OptionalPages,
		ProhibitedPageTypes: app.ProhibitedPageTypes,
		BaseOperations:      app.BaseOperations,
		Shell:               app.Shell,
		Palette:             k.Palettes[app.Palette], PaletteName: app.Palette,
		Roles: roles, Entities: entities, Features: features,
		Pages: app.DefaultPages, Operations: app.Operations,
		Domain: domainKey, DomainLabel: domain.Label, DomainConfidence: confidence,
		DomainModules: domain.Modules, DomainTables: domain.Tables,
		DomainWorkflows:   domain.Workflows,
		DomainPublicPages: domain.PublicPages, DomainProtectedPages: domain.ProtectedPages,
		DomainIntegrations: domain.Integrations,
	}
}

// DefaultAppType is what an unrecognised key falls back to.
const DefaultAppType = "saas"

// singularPascal turns `sales_orders` into `SalesOrder`.
func singularPascal(tableName string) string {
	name := singular(strings.TrimSpace(tableName))
	parts := strings.Split(name, "_")
	for i, part := range parts {
		if part != "" {
			parts[i] = strings.ToUpper(part[:1]) + part[1:]
		}
	}
	return strings.Join(parts, "")
}
