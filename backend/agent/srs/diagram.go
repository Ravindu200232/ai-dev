package srs

import (
	"regexp"
	"strings"
)

// Eleven diagrams, each written twice: once as Mermaid, which is the source of
// record and what the Studio can re-render, and once as vector shapes drawn
// straight to SVG and into the PDF (draw.go).
//
// Nothing here invents structure. A state machine with no stated transitions
// says so rather than drawing a plausible lifecycle, because a diagram that
// looks authoritative and was guessed at is worse than no diagram.

// DiagramKinds is the fixed set, in the order the document presents them.
var DiagramKinds = []struct {
	Kind  string
	Title string
}{
	{"use_case", "Use Case Diagram"},
	{"sequence", "Sequence Diagram"},
	{"erd", "Entity-Relationship (ER) Diagram"},
	{"activity", "Activity Diagram"},
	{"class_object", "Class & Object Diagram"},
	{"state_machine", "State Machine Diagram"},
	{"dfd", "Data Flow Diagram (DFD)"},
	{"bpmn", "BPMN Process Diagram"},
	{"system_context", "System Context Diagram"},
	{"component", "Component Diagram"},
	{"deployment", "Deployment Diagram"},
}

const maxActors = 7
const maxUseCases = 14

const labelLimit = 60
const edgeLabelLimit = 40

var unsafeLabel = regexp.MustCompile("[\"'\\[\\]{}()|<>#;`]")
var identifierUnsafe = regexp.MustCompile(`[^A-Za-z0-9_]`)
var leadingArticle = regexp.MustCompile(`(?i)^(?:the|a|an)\s+`)

// san is a label safe to put inside a Mermaid quoted string.
func san(text string, limit int) string {
	text = strings.TrimSpace(strings.Join(strings.Fields(
		strings.ReplaceAll(unsafeLabel.ReplaceAllString(text, ""), "\n", " ")), " "))
	if len([]rune(text)) <= limit {
		return text
	}
	runes := []rune(text)[:limit]
	cut := string(runes)
	if i := strings.LastIndex(cut, " "); i > 0 {
		cut = strings.TrimRight(cut[:i], ",;:-")
	}
	if cut == "" {
		cut = string(runes)
	}
	return cut + "…"
}

func entityID(name string) string {
	out := identifierUnsafe.ReplaceAllString(name, "_")
	if out == "" {
		return "entity"
	}
	return out
}

// diagramPlan is the plan these diagrams are drawn from: the specification as
// it stands now if it has been customized, otherwise the one approved.
func diagramPlan(doc *Document) *Plan {
	if len(doc.EffectivePlan) > 0 {
		return planFromDoc(doc.EffectivePlan)
	}
	return planFromDoc(doc.ApprovedPlan)
}

func verbPhrase(text string) string {
	return leadingArticle.ReplaceAllString(strings.TrimRight(strings.TrimSpace(text), "."), "")
}

// --- who does what ------------------------------------------------------------------

type actor struct {
	ID    string
	Label string
	Does  []string
}

type useCase struct {
	ID    string
	Label string
}

// actorsAndUseCases is who uses the system and what each of them does. Duties
// come from the plan's roles; only when nobody has one does it fall back to
// features, because a feature is not the same thing as somebody's goal.
func actorsAndUseCases(doc *Document) ([]actor, []useCase) {
	plan := diagramPlan(doc)
	var cases []useCase
	byText := map[string]string{}

	useCaseID := func(text string) string {
		label := san(verbPhrase(text), labelLimit)
		if label == "" {
			return ""
		}
		key := strings.ToLower(label)
		if id, ok := byText[key]; ok {
			return id
		}
		if len(cases) >= maxUseCases {
			return ""
		}
		id := "U" + itoa(len(cases))
		byText[key] = id
		cases = append(cases, useCase{ID: id, Label: label})
		return id
	}

	people := plan.Users
	if len(people) == 0 {
		allowed := map[string][]string{}
		for _, m := range doc.RoleAccessMatrix {
			allowed[m.Role] = m.AllowedFunctions
		}
		for _, r := range doc.Roles {
			people = append(people, PlanUser{
				Role: firstNonEmpty(r.RoleName, r.RoleKey), CanDo: allowed[r.RoleKey],
			})
		}
	}
	if len(people) > maxActors {
		people = people[:maxActors]
	}

	var actors []actor
	for _, person := range people {
		label := san(firstNonEmpty(person.Role, "User"), 28)
		if label == "" {
			continue
		}
		var does []string
		for _, duty := range person.CanDo {
			if id := useCaseID(duty); id != "" {
				does = append(does, id)
			}
		}
		actors = append(actors, actor{ID: "A" + itoa(len(actors)), Label: label, Does: does})
	}
	if len(actors) == 0 {
		actors = []actor{{ID: "A0", Label: "User"}}
	}

	busy := false
	for _, a := range actors {
		if len(a.Does) > 0 {
			busy = true
		}
	}
	if !busy {
		var fallback []string
		for _, f := range plan.Features {
			if id := useCaseID(f); id != "" {
				fallback = append(fallback, id)
			}
		}
		if len(fallback) == 0 {
			for _, m := range doc.MainModules {
				if id := useCaseID(m); id != "" {
					fallback = append(fallback, id)
				}
			}
		}
		for i := range actors {
			actors[i].Does = fallback
		}
	}
	return actors, cases
}

// --- the sources ----------------------------------------------------------------------

func useCaseSource(doc *Document) string {
	actors, cases := actorsAndUseCases(doc)
	lines := []string{
		"flowchart LR",
		"  classDef actor fill:#EEF2FF,stroke:#6366F1,color:#3730A3;",
		"  classDef uc fill:#F8FAFC,stroke:#94A3B8,color:#0F172A;",
		`  subgraph SYSTEM["` + san(firstNonEmpty(doc.ProjectName, "System"), 40) + `"]`,
		"    direction TB",
	}
	for _, uc := range cases {
		lines = append(lines, `    `+uc.ID+`(["`+uc.Label+`"]):::uc`)
	}
	lines = append(lines, "  end")
	for _, a := range actors {
		lines = append(lines, `  `+a.ID+`["`+a.Label+`"]:::actor`)
		for _, uid := range a.Does {
			lines = append(lines, "  "+a.ID+" --- "+uid)
		}
	}
	return strings.Join(lines, "\n")
}

var conditionWord = regexp.MustCompile(`(?i)\b(if|whether|when)\b|\?`)
var alternativeWord = regexp.MustCompile(`(?i)\b(else|otherwise|if not|on failure|on rejection)\b`)

// isDecision is a step that states both a condition and what happens when it
// does not hold. One without the other is a statement, not a branch.
func isDecision(step string) bool {
	return conditionWord.MatchString(step) && alternativeWord.MatchString(step)
}

// firstFlow is the workflow the ordered diagrams are drawn from.
func firstFlow(doc *Document) (title string, steps []string) {
	if len(doc.BusinessWorkflows) > 0 {
		w := doc.BusinessWorkflows[0]
		return firstNonEmpty(w.WorkflowName, "Main Workflow"), w.Steps
	}
	plan := diagramPlan(doc)
	if len(plan.Workflows) > 0 {
		w := plan.Workflows[0]
		return firstNonEmpty(w.Name, "Main Workflow"), w.Steps
	}
	features := plan.Features
	if len(features) > 6 {
		features = features[:6]
	}
	return "Using the application", features
}

func activitySource(doc *Document) string {
	title, steps := firstFlow(doc)
	steps = cleanList(steps)
	if len(steps) > 9 {
		steps = steps[:9]
	}
	if len(steps) == 0 {
		steps = []string{"Open the application", "Do the main task", "See the result"}
	}

	lines := []string{
		"flowchart TD",
		"  classDef dec fill:#FEF3C7,stroke:#F59E0B,color:#92400E;",
		`  START(["Start: ` + san(title, 48) + `"])`,
	}
	prev := "START"
	for i, step := range steps {
		id := "s" + itoa(i)
		if isDecision(step) {
			lines = append(lines,
				`  `+id+`{"`+san(step, labelLimit)+`?"}:::dec`,
				"  "+prev+" --> "+id,
				`  `+id+` -->|yes| `+id+`_ok["Continue"]`,
				`  `+id+` -->|no| `+id+`_no["Correct and retry"]`,
				"  "+id+"_no --> "+id)
			prev = id + "_ok"
			continue
		}
		lines = append(lines, `  `+id+`["`+san(step, labelLimit)+`"]`, "  "+prev+" --> "+id)
		prev = id
	}
	return strings.Join(append(lines, `  DONE(["End"])`, "  "+prev+" --> DONE"), "\n")
}

// realEndpoints keeps the sequence diagram from saying `POST /api` for
// everything. Auth routes are left out: they are the same on every diagram.
func realEndpoints(doc *Document) [][2]string {
	var out [][2]string
	for _, ep := range doc.APIDesign {
		if ep.Method != "" && ep.Path != "" && !strings.Contains(ep.Path, "/auth/") {
			out = append(out, [2]string{ep.Method, ep.Path})
		}
	}
	return out
}

func sequenceSource(doc *Document) string {
	_, steps := firstFlow(doc)
	steps = cleanList(steps)
	if len(steps) == 0 {
		steps = []string{"Perform the primary interaction"}
	}
	if len(steps) > 6 {
		steps = steps[:6]
	}

	role := "User"
	for _, r := range doc.Roles {
		if name := firstNonEmpty(r.RoleName, r.RoleKey); name != "" {
			role = name
			break
		}
	}
	system := san(firstNonEmpty(doc.ProjectName, "System"), 28)
	tables := doc.DatabaseDesign.Tables
	endpoints := realEndpoints(doc)
	integrations := doc.IntegrationRequirements

	lines := []string{"sequenceDiagram", "  autonumber",
		"  actor U as " + san(role, 24), "  participant S as " + system}
	if len(endpoints) > 0 {
		lines = append(lines, "  participant A as Application API")
	}
	if len(tables) > 0 {
		lines = append(lines, "  participant DB as Database")
	}
	if len(integrations) > 0 {
		lines = append(lines, "  participant X as "+san(firstNonEmpty(integrations[0].Name, "External System"), 24))
	}

	for i, step := range steps {
		lines = append(lines, "  U->>S: "+san(step, 48), "  activate S")
		switch {
		case len(endpoints) > 0:
			ep := endpoints[i%len(endpoints)]
			lines = append(lines, "  S->>A: "+ep[0]+" "+ep[1], "  activate A")
			if len(tables) > 0 {
				lines = append(lines, "  A->>DB: access "+san(tables[i%len(tables)].TableName, 24),
					"  DB-->>A: data / result")
			}
			if len(integrations) > 0 && mentionsIntegration(step, integrations[0]) {
				lines = append(lines, "  A->>X: integration request", "  X-->>A: integration response")
			}
			lines = append(lines, "  A-->>S: result", "  deactivate A")
		case len(tables) > 0:
			lines = append(lines, "  S->>DB: access "+san(tables[i%len(tables)].TableName, 24),
				"  DB-->>S: data / result")
		}
		lines = append(lines, "  S-->>U: present outcome", "  deactivate S")
	}
	return strings.Join(lines, "\n")
}

func mentionsIntegration(step string, integration Integration) bool {
	low := strings.ToLower(step)
	for _, word := range []string{integration.Name, integration.Type} {
		if word = strings.ToLower(strings.TrimSpace(word)); word != "" && strings.Contains(low, word) {
			return true
		}
	}
	return false
}

var cardinality = map[string]string{
	"one_to_many":  "||--o{",
	"many_to_one":  "}o--||",
	"one_to_one":   "||--||",
	"many_to_many": "}o--o{",
}

func erdSource(doc *Document) string {
	tables := doc.DatabaseDesign.Tables
	if len(tables) > 22 {
		tables = tables[:22]
	}
	names := map[string]bool{}
	for _, t := range tables {
		names[t.TableName] = true
	}

	lines := []string{"erDiagram"}
	for _, t := range tables {
		lines = append(lines, "  "+entityID(firstNonEmpty(t.TableName, "entity"))+" {")
		fields := t.Fields
		if len(fields) > 14 {
			fields = fields[:14]
		}
		for _, f := range fields {
			name := identifierUnsafe.ReplaceAllString(firstNonEmpty(f.Name, "field"), "_")
			raw := firstNonEmpty(f.Type, "string")
			kind := identifierUnsafe.ReplaceAllString(raw, "_")
			if raw == "foreign_key" {
				kind = "uuid"
			}
			key := ""
			switch {
			case f.PrimaryKey:
				key = " PK"
			case raw == "foreign_key" || f.References != "":
				key = " FK"
			case f.Unique:
				key = " UK"
			}
			lines = append(lines, "    "+kind+" "+name+key)
		}
		lines = append(lines, "  }")
	}

	seen := map[string]bool{}
	for _, rel := range doc.DatabaseDesign.Relationships {
		a := strings.SplitN(rel.From, ".", 2)[0]
		b := strings.SplitN(rel.To, ".", 2)[0]
		if !names[a] || !names[b] || a == b {
			continue
		}
		card, ok := cardinality[rel.Type]
		if !ok {
			card = "||--o{"
		}
		edge := a + card + b
		if seen[edge] {
			continue
		}
		seen[edge] = true
		label := san(rel.Description, edgeLabelLimit)
		if label == "" || strings.Contains(label, "…") {
			label = "relates to"
		}
		lines = append(lines, "  "+entityID(a)+" "+card+" "+entityID(b)+` : "`+label+`"`)
	}
	return strings.Join(lines, "\n")
}

func classSource(doc *Document) string {
	tables := doc.DatabaseDesign.Tables
	if len(tables) > 10 {
		tables = tables[:10]
	}
	names := map[string]string{}
	lines := []string{"classDiagram"}
	for _, t := range tables {
		name := className(t.TableName)
		names[t.TableName] = name
		lines = append(lines, "  class "+name+" {")
		fields := t.Fields
		if len(fields) > 10 {
			fields = fields[:10]
		}
		for _, f := range fields {
			visibility := "-"
			if f.PrimaryKey {
				visibility = "+"
			}
			kind := classType.ReplaceAllString(firstNonEmpty(f.Type, "String"), "")
			if kind == "" {
				kind = "String"
			}
			lines = append(lines, "    "+visibility+kind+" "+
				identifierUnsafe.ReplaceAllString(firstNonEmpty(f.Name, "field"), "_"))
		}
		lines = append(lines, "  }")
	}

	multiplicity := map[string]string{
		"one_to_many":  `"1" --> "0..*"`,
		"many_to_one":  `"0..*" --> "1"`,
		"one_to_one":   `"1" --> "1"`,
		"many_to_many": `"0..*" --> "0..*"`,
	}
	relationships := doc.DatabaseDesign.Relationships
	if len(relationships) > 16 {
		relationships = relationships[:16]
	}
	for _, rel := range relationships {
		a := strings.SplitN(rel.From, ".", 2)[0]
		b := strings.SplitN(rel.To, ".", 2)[0]
		if names[a] == "" || names[b] == "" || a == b {
			continue
		}
		mult, ok := multiplicity[rel.Type]
		if !ok {
			mult = `"1" --> "0..*"`
		}
		lines = append(lines, "  "+names[a]+" "+mult+" "+names[b]+" : "+
			san(firstNonEmpty(rel.Description, "relates to"), 32))
	}
	return strings.Join(lines, "\n")
}

var classType = regexp.MustCompile(`[^A-Za-z0-9_<>,]`)

func className(table string) string {
	return entityID(strings.ReplaceAll(titleCase(strings.ReplaceAll(table, "_", " ")), " ", ""))
}

// --- lifecycles ------------------------------------------------------------------------

type lifecycle struct {
	Entity string
	Field  string
	States []string
}

var lifecycleField = regexp.MustCompile(`(?i)(status|state|stage|phase|lifecycle)`)

// firstLifecycle is the status column whose transitions the specification
// actually talks about, or the first one it has if none are described.
func firstLifecycle(doc *Document) *lifecycle {
	var candidates []lifecycle
	for _, table := range doc.DatabaseDesign.Tables {
		for _, f := range table.Fields {
			var states []string
			for _, v := range f.Values {
				if text := strings.TrimSpace(firstText(v)); text != "" {
					states = append(states, text)
				}
			}
			if len(states) >= 2 && lifecycleField.MatchString(f.Name) {
				if len(states) > 8 {
					states = states[:8]
				}
				candidates = append(candidates, lifecycle{
					Entity: firstNonEmpty(table.TableName, "Entity"), Field: f.Name, States: states,
				})
			}
		}
	}
	if len(candidates) == 0 {
		return nil
	}
	blob := lifecycleText(doc)
	for _, c := range candidates {
		for _, a := range c.States {
			for _, b := range c.States {
				if a != b && statedTransition(blob, a, b) {
					return &c
				}
			}
		}
	}
	return &candidates[0]
}

func lifecycleText(doc *Document) string {
	var rows []string
	for _, r := range doc.FunctionalRequirements {
		rows = append(rows, r.Requirement)
	}
	for _, w := range doc.BusinessWorkflows {
		rows = append(rows, w.Steps...)
	}
	return strings.Join(rows, "\n")
}

// statedTransition is whether the text literally says one state becomes
// another. Nothing else counts: a lifecycle nobody described is not drawn.
func statedTransition(text, from, to string) bool {
	from, to = regexp.QuoteMeta(from), regexp.QuoteMeta(to)
	for _, pattern := range []string{
		`(?i)\bfrom\s+` + from + `\s+to\s+` + to + `\b`,
		`(?i)\b` + from + `\s*(?:->|→)\s*` + to + `\b`,
	} {
		if regexp.MustCompile(pattern).MatchString(text) {
			return true
		}
	}
	return false
}

type transition struct{ From, To, Label string }

func stateTransitions(doc *Document, life *lifecycle) []transition {
	if life == nil || len(life.States) == 0 {
		return nil
	}
	var rows []string
	for _, r := range doc.FunctionalRequirements {
		rows = append(rows, r.Requirement)
	}
	for _, w := range doc.BusinessWorkflows {
		rows = append(rows, w.Steps...)
	}

	var found []transition
	for _, text := range rows {
		for _, a := range life.States {
			for _, b := range life.States {
				if a == b || !statedTransition(text, a, b) {
					continue
				}
				item := transition{a, b, firstNonEmpty(san(text, 42), "transition")}
				duplicate := false
				for _, existing := range found {
					if existing == item {
						duplicate = true
					}
				}
				if !duplicate {
					found = append(found, item)
				}
			}
		}
	}
	if len(found) > 12 {
		found = found[:12]
	}
	return found
}

func stateMachineSource(doc *Document) string {
	life := firstLifecycle(doc)
	transitions := stateTransitions(doc, life)
	if life == nil || len(transitions) == 0 {
		return strings.Join([]string{
			"stateDiagram-v2",
			"  [*] --> NotApplicable",
			"  NotApplicable : No explicit legal state transitions specified",
			"  NotApplicable --> [*]",
		}, "\n")
	}
	ids := map[string]string{}
	lines := []string{"stateDiagram-v2"}
	for i, state := range life.States {
		ids[state] = "S" + itoa(i)
		lines = append(lines, "  "+ids[state]+" : "+san(state, 36))
	}
	lines = append(lines, "  [*] --> "+ids[transitions[0].From])
	for _, t := range transitions {
		lines = append(lines, "  "+ids[t.From]+" --> "+ids[t.To]+" : "+san(t.Label, 32))
	}
	return strings.Join(lines, "\n")
}

// sourceFor writes one diagram's Mermaid.
func sourceFor(kind string, doc *Document) string {
	switch kind {
	case "use_case":
		return useCaseSource(doc)
	case "sequence":
		return sequenceSource(doc)
	case "erd":
		return erdSource(doc)
	case "activity":
		return activitySource(doc)
	case "class_object":
		return classSource(doc)
	case "state_machine":
		return stateMachineSource(doc)
	case "dfd":
		return dfdSource(doc)
	case "bpmn":
		return bpmnSource(doc)
	case "system_context":
		return systemContextSource(doc)
	case "component":
		return componentSource(doc)
	case "deployment":
		return deploymentSource(doc)
	}
	return ""
}
