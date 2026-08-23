package srs

import (
	"strings"
	"testing"
)

func diagramDoc(t *testing.T) *Document {
	t.Helper()
	doc := composed(t)
	doc.BusinessWorkflows = []Workflow{{
		WorkflowName: "Taking a sale", Who: "Cashier",
		Steps: []string{"Cashier opens the Sale Terminal", "Cashier takes payment", "System prints a receipt"},
	}}
	doc.IntegrationRequirements = []Integration{{Name: "Card Reader", Type: "payment"}}
	doc.NotificationRules = []Notification{{Event: "sale.completed", Channels: []string{"email"}}}
	return doc
}

func TestBuildDiagramsProducesValidMermaid(t *testing.T) {
	doc := diagramDoc(t)
	var failures []string
	diagrams := BuildDiagrams(doc, func(msg string) { failures = append(failures, msg) })

	if len(diagrams) != 11 {
		t.Fatalf("diagrams = %d, want 11", len(diagrams))
	}
	if len(failures) > 0 {
		t.Errorf("every template must produce valid Mermaid:\n%s", strings.Join(failures, "\n"))
	}
	for i, d := range diagrams {
		if d.Kind != DiagramKinds[i].Kind || d.Title != DiagramKinds[i].Title {
			t.Errorf("diagram %d = %q/%q", i, d.Kind, d.Title)
		}
		if d.ID != "dia_"+d.Kind || d.Format != "mermaid" || d.CanonicalRendering != "native_svg" {
			t.Errorf("diagram = %+v", d)
		}
		if d.Standard == "" {
			t.Errorf("%s has no notation named", d.Kind)
		}
		if problems := MermaidProblems(d.Kind, d.Source); len(problems) > 0 {
			t.Errorf("%s: %v\n%s", d.Kind, problems, d.Source)
		}
	}
}

func TestDiagramApplicability(t *testing.T) {
	doc := diagramDoc(t)
	// This document has no status column anybody described transitions for.
	if ok, reason := DiagramApplicable("state_machine", doc); ok || reason == "" {
		t.Errorf("state machine = %v %q", ok, reason)
	}
	if ok, _ := DiagramApplicable("erd", doc); !ok {
		t.Error("this document has tables")
	}
	if ok, _ := DiagramApplicable("sequence", doc); !ok {
		t.Error("this document has a workflow")
	}

	empty := &Document{}
	for _, kind := range []string{"erd", "activity", "bpmn", "sequence"} {
		if ok, reason := DiagramApplicable(kind, empty); ok || reason == "" {
			t.Errorf("%s on an empty document = %v %q", kind, ok, reason)
		}
	}
	if ok, _ := DiagramApplicable("use_case", empty); !ok {
		t.Error("a use case diagram is always drawable")
	}
}

func TestStateMachineOnlyDrawsStatedTransitions(t *testing.T) {
	doc := &Document{DatabaseDesign: DatabaseDesign{Tables: []Table{{
		TableName: "orders",
		Fields:    []Field{{Name: "status", Type: "enum", Values: []any{"draft", "paid", "shipped"}}},
	}}}}

	// Nothing says how an order moves, so nothing is drawn.
	source := stateMachineSource(doc)
	if !strings.Contains(source, "NotApplicable") {
		t.Errorf("an undescribed lifecycle must not be invented:\n%s", source)
	}
	if problems := MermaidProblems("state_machine", source); len(problems) > 0 {
		t.Errorf("%v", problems)
	}

	doc.FunctionalRequirements = []Requirement{
		{ID: "FR-001", Requirement: "The system shall move an order from draft to paid on payment."},
		{ID: "FR-002", Requirement: "The system shall move paid -> shipped when it leaves."},
	}
	source = stateMachineSource(doc)
	if strings.Contains(source, "NotApplicable") {
		t.Errorf("a described lifecycle must be drawn:\n%s", source)
	}
	if strings.Count(source, "-->") != 3 {
		t.Errorf("two transitions plus the initial state:\n%s", source)
	}
	if ok, _ := DiagramApplicable("state_machine", doc); !ok {
		t.Error("a described lifecycle is applicable")
	}
}

func TestUseCasesComeFromDuties(t *testing.T) {
	doc := composed(t)
	actors, cases := actorsAndUseCases(doc)
	var names []string
	for _, a := range actors {
		names = append(names, a.Label)
	}
	if strings.Join(names, ",") != "Cashier,Admin" {
		t.Fatalf("actors = %v", names)
	}
	if len(cases) == 0 {
		t.Fatal("nobody does anything")
	}
	var labels []string
	for _, c := range cases {
		labels = append(labels, c.Label)
	}
	if !containsString(labels, "Take a sale") {
		t.Errorf("use cases = %v", labels)
	}
	if len(actors[0].Does) == 0 {
		t.Error("the cashier's duties are lost")
	}

	// With nobody's duties recorded, features stand in — but only then.
	bare := &Document{ApprovedPlan: asDoc(&Plan{
		Users:    []PlanUser{{Role: "Visitor"}},
		Features: []string{"Browse the catalogue"},
	})}
	_, fallback := actorsAndUseCases(bare)
	if len(fallback) != 1 || fallback[0].Label != "Browse the catalogue" {
		t.Errorf("fallback = %+v", fallback)
	}
}

func TestSequenceUsesRealEndpoints(t *testing.T) {
	doc := diagramDoc(t)
	source := sequenceSource(doc)
	if !strings.Contains(source, "GET /api/products") && !strings.Contains(source, "POST /api/products") {
		t.Errorf("the sequence should name real endpoints:\n%s", source)
	}
	if strings.Contains(source, "/api/auth/") {
		t.Error("auth routes are the same on every diagram and are left out")
	}
	if !strings.Contains(source, "participant DB as Database") {
		t.Error("a document with tables has a database participant")
	}
	if problems := MermaidProblems("sequence", source); len(problems) > 0 {
		t.Errorf("%v\n%s", problems, source)
	}
}

func TestERDCardinality(t *testing.T) {
	doc := diagramDoc(t)
	source := erdSource(doc)
	if !strings.Contains(source, "sales }o--|| products") {
		t.Errorf("many-to-one should read }o--||:\n%s", source)
	}
	if !strings.Contains(source, "uuid id PK") {
		t.Errorf("the primary key must be marked:\n%s", source)
	}
	if !strings.Contains(source, "FK") {
		t.Errorf("a foreign key must be marked:\n%s", source)
	}
}

func TestMermaidProblemsCatchesRealFailures(t *testing.T) {
	cases := []struct {
		name, kind, source, want string
	}{
		{"stub", "erd", "erDiagram", "empty or a stub"},
		{"wrong header", "erd", "flowchart TD\n  a --> b\n  b --> c\n  c --> d", "must start with erDiagram"},
		{"unclosed entity", "erd", "erDiagram\n  users {\n    uuid id PK\n    string name\n", "opened"},
		{"no classes", "class_object", "classDiagram\n  %% nothing here at all, really nothing", "no classes"},
		{"no pseudo-state", "state_machine", "stateDiagram-v2\n  S0 : Draft\n  S0 --> S1\n  S1 : Paid", "pseudo-state"},
		{"unbalanced activate", "sequence", "sequenceDiagram\n  actor U as User\n  participant S as System\n  U->>S: hello\n  activate S", "activate vs"},
		{"undeclared participant", "sequence", "sequenceDiagram\n  actor U as User\n  participant S as System\n  U->>Q: hello there", "undeclared"},
		{"unclosed subgraph", "component", "flowchart TB\n  subgraph A[\"x\"]\n    P0[\"one\"]\n  P0 --> GW\n  GW[\"gw\"]", "subgraph vs"},
		{"orphan node", "component", "flowchart TB\n  P0[\"one\"]\n  A[\"a\"]\n  B[\"b\"]\n  A --> B", "unconnected"},
		{"unbalanced quotes", "component", "flowchart TB\n  P0[\"one]\n  A --> B\n  A[\"a\"]\n  B[\"b\"]", "unbalanced quotes"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			problems := MermaidProblems(tc.kind, tc.source)
			if len(problems) == 0 {
				t.Fatalf("expected a problem naming %q", tc.want)
			}
			if !strings.Contains(strings.Join(problems, "; "), tc.want) {
				t.Errorf("problems = %v, want one naming %q", problems, tc.want)
			}
		})
	}

	good := "flowchart TB\n  A[\"one\"]\n  B[\"two\"]\n  A --> B"
	if !ValidMermaid("component", good) {
		t.Errorf("a sound flowchart should pass: %v", MermaidProblems("component", good))
	}
}

func TestSanitiseLabels(t *testing.T) {
	if got := san(`a "quoted" [label]`, 60); got != "a quoted label" {
		t.Errorf("san = %q", got)
	}
	if got := san("one   two\nthree", 60); got != "one two three" {
		t.Errorf("san = %q", got)
	}
	long := san(strings.Repeat("word ", 30), 20)
	if !strings.HasSuffix(long, "…") || len([]rune(long)) > 21 {
		t.Errorf("san = %q", long)
	}
	if got := entityID("sale items!"); got != "sale_items_" {
		t.Errorf("entityID = %q", got)
	}
	if got := entityID("!!!"); got != "___" {
		t.Errorf("entityID = %q", got)
	}
	if got := verbPhrase("The take a sale."); got != "take a sale" {
		t.Errorf("verbPhrase = %q", got)
	}
}

func TestDecisionNeedsBothSides(t *testing.T) {
	if isDecision("If the payment succeeds") {
		t.Error("a condition with no alternative is a statement, not a branch")
	}
	if !isDecision("If the payment succeeds, otherwise show the error") {
		t.Error("both sides stated should read as a decision")
	}
	source := activitySource(&Document{BusinessWorkflows: []Workflow{{
		WorkflowName: "Paying",
		Steps: []string{
			"Open the till",
			"If the card is accepted, otherwise ask for another",
			"Print the receipt",
		},
	}}})
	if !strings.Contains(source, ":::dec") || !strings.Contains(source, "-->|no|") {
		t.Errorf("the decision is missing:\n%s", source)
	}
	if problems := MermaidProblems("activity", source); len(problems) > 0 {
		t.Errorf("%v\n%s", problems, source)
	}
}
