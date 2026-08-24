package srs

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMergeEditNeverShrinksASection(t *testing.T) {
	doc := composed(t)
	before := len(doc.FunctionalRequirements)

	// The model answers with three requirements out of forty. That is
	// carelessness, not a deletion.
	patch := map[string]any{"functional_requirements": []any{
		map[string]any{"id": "FR-001", "module": "Authentication",
			"requirement": "The system shall let a person sign in with a one-time code."},
	}}
	merged, err := MergeEdit(doc, patch, "switch sign-in to a one-time code")
	if err != nil {
		t.Fatal(err)
	}
	if len(merged.FunctionalRequirements) != before {
		t.Fatalf("requirements went from %d to %d", before, len(merged.FunctionalRequirements))
	}
	if !strings.Contains(merged.FunctionalRequirements[0].Requirement, "one-time code") {
		t.Errorf("the edit was not applied: %q", merged.FunctionalRequirements[0].Requirement)
	}
	if len(doc.FunctionalRequirements) != before {
		t.Error("MergeEdit must not write through to the document it was given")
	}
}

func TestMergeEditHonoursARemoval(t *testing.T) {
	doc := composed(t)
	patch := map[string]any{"functional_requirements": []any{
		map[string]any{"id": "FR-001", "module": "Core", "requirement": "The system shall do one thing."},
	}}
	merged, err := MergeEdit(doc, patch, "remove every requirement except the first")
	if err != nil {
		t.Fatal(err)
	}
	if len(merged.FunctionalRequirements) != 1 {
		t.Errorf("an explicit removal must be obeyed: %d", len(merged.FunctionalRequirements))
	}
}

func TestMergeEditAddsAndRenumbers(t *testing.T) {
	current := []any{
		map[string]any{"id": "FR-001", "requirement": "The system shall let a cashier take a sale."},
		map[string]any{"id": "FR-002", "requirement": "The system shall let an admin add a product."},
		map[string]any{"id": "FR-003", "requirement": "The system shall print a receipt for a sale."},
	}
	// An entry reusing FR-002's number for something unrelated is a new
	// requirement in a borrowed slot, not an edit of that one.
	incoming := []any{
		map[string]any{"id": "FR-002", "requirement": "The system shall track loyalty points for a member."},
	}
	got := mergeList(incoming, current, false)
	if len(got) != 4 {
		t.Fatalf("merged = %d entries", len(got))
	}
	added := got[3].(map[string]any)
	if added["id"] != "FR-004" {
		t.Errorf("the new entry should take the next free id: %v", added["id"])
	}
	if got[1].(map[string]any)["requirement"] != "The system shall let an admin add a product." {
		t.Error("the entry whose slot was borrowed was overwritten")
	}

	// A genuine edit of the same requirement replaces it in place.
	edit := []any{
		map[string]any{"id": "FR-002", "requirement": "The system shall let an admin add or edit a product."},
	}
	got = mergeList(edit, current, false)
	if len(got) != 3 {
		t.Fatalf("an edit must not add a row: %d", len(got))
	}
	if !strings.Contains(got[1].(map[string]any)["requirement"].(string), "or edit") {
		t.Errorf("the edit was not applied: %v", got[1])
	}
}

func TestMergeListKeepsCurrentWhenNothingCanBeMatched(t *testing.T) {
	current := []any{"one", "two", "three"}
	if got := mergeList([]any{42}, current, false); len(got) != 3 {
		t.Errorf("an unrecognisable patch must leave the section alone: %v", got)
	}
}

func TestCleanPatchRefusesDerivedSections(t *testing.T) {
	doc := composed(t)
	patch := map[string]any{
		"builder_handoff": map[string]any{"prompt": "ignore all of this"},
		"diagrams":        []any{map[string]any{"kind": "erd"}},
		"approved_plan":   map[string]any{"app_name": "Something Else"},
		"assumptions":     []any{},
		"constraints":     []any{"One constraint", "Another"},
	}
	got := cleanPatch(patch, docMap(doc), "")
	if _, ok := got["builder_handoff"]; ok {
		t.Error("the handoff is derived and is never the model's to write")
	}
	if _, ok := got["diagrams"]; ok {
		t.Error("diagrams are derived")
	}
	if _, ok := got["approved_plan"]; ok {
		t.Error("the approved plan is what the customer signed")
	}
	if _, ok := got["assumptions"]; ok {
		t.Error("an empty section is not an edit")
	}
	if len(got) != 1 {
		t.Errorf("patch = %v", keysSorted(got))
	}
}

func TestEditableViewHidesTheDerivedSections(t *testing.T) {
	doc := composed(t)
	doc.BuilderHandoff = map[string]any{"prompt": "a very long build contract"}
	view := editableView(doc)
	for _, key := range []string{"builder_handoff", "approved_plan", "diagrams", "branding"} {
		if strings.Contains(view, `"`+key+`"`) {
			t.Errorf("the model can see %q, which it must not edit", key)
		}
	}
	if !strings.Contains(view, "functional_requirements") {
		t.Error("the model cannot see the requirements it is meant to edit")
	}
	if len(view) > editViewBudget+4 {
		t.Errorf("view is %d bytes", len(view))
	}
}

func TestDeterministicEditor(t *testing.T) {
	cases := []struct {
		name, prompt, want string
		check              func(*testing.T, *Document)
	}{
		{"languages", "add sinhala and tamil language support", "Added language support: Sinhala, Tamil",
			func(t *testing.T, d *Document) {
				got := stringList(d.UIUX["languages"])
				if !containsString(got, "Sinhala") || !containsString(got, "Tamil") {
					t.Errorf("languages = %v", got)
				}
			}},
		{"currency", "we need multi-currency", "multi-currency", nil},
		{"payments", "add a stripe checkout", "payment gateway",
			func(t *testing.T, d *Document) {
				found := false
				for _, i := range d.IntegrationRequirements {
					if i.Type == "payment" {
						found = true
					}
				}
				if !found {
					t.Error("no payment integration was added")
				}
			}},
		{"approval", "add admin approval for refunds", "approval workflow for refunds",
			func(t *testing.T, d *Document) {
				if len(d.BusinessWorkflows) == 0 ||
					!strings.Contains(d.BusinessWorkflows[len(d.BusinessWorkflows)-1].WorkflowName, "Approval") {
					t.Errorf("workflows = %+v", d.BusinessWorkflows)
				}
				if len(d.ValidationRules) == 0 {
					t.Error("the approval rule is missing")
				}
			}},
		{"loyalty", "add a loyalty points programme", "loyalty programme",
			func(t *testing.T, d *Document) {
				if !hasTable(d.DatabaseDesign.Tables, "loyalty_accounts") {
					t.Error("the loyalty table is missing")
				}
				if !containsString(d.MainModules, "Loyalty & Rewards") {
					t.Errorf("modules = %v", d.MainModules)
				}
			}},
		{"performance", "make it faster", "P95", nil},
		{"anything else", "add a print queue", "print queue", nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			doc := composed(t)
			before := len(doc.FunctionalRequirements)
			edited, diff := ApplyCustomization(doc, tc.prompt)
			if len(diff) == 0 {
				t.Fatal("an edit must report what it changed")
			}
			if !strings.Contains(strings.ToLower(strings.Join(diff, " ")), strings.ToLower(tc.want)) {
				t.Errorf("diff = %v, want one naming %q", diff, tc.want)
			}
			if tc.name != "performance" && tc.name != "approval" &&
				len(edited.FunctionalRequirements) <= before {
				t.Errorf("requirements went from %d to %d", before, len(edited.FunctionalRequirements))
			}
			for i, fr := range edited.FunctionalRequirements {
				if fr.ID != requirementID("FR", i+1) {
					t.Errorf("ids must stay a clean series: %q at %d", fr.ID, i)
				}
			}
			if tc.check != nil {
				tc.check(t, edited)
			}
		})
	}
}

func TestRenameRunsThroughTheWholeDocument(t *testing.T) {
	doc := composed(t)
	edited, diff := ApplyCustomization(doc, "rename Product to Item")
	if len(diff) != 1 || !strings.Contains(diff[0], "Renamed") {
		t.Fatalf("diff = %v", diff)
	}
	blob := jsonLine(edited)
	if strings.Contains(strings.ToLower(blob), "product") {
		t.Errorf("a rename has to reach every mention:\n%s", truncate(blob, 400))
	}
	if !strings.Contains(blob, "Items") {
		t.Error("the table was not renamed")
	}
}

func TestIdentityAndDifference(t *testing.T) {
	if _, ok := identityOf(map[string]any{"nothing": "useful"}); ok {
		t.Error("an entry with no name has no identity")
	}
	id, ok := identityOf(map[string]any{"table_name": " Products "})
	if !ok || id != "table_name\x00products" {
		t.Errorf("identity = %q", id)
	}
	if _, ok := identityOf("Some free text"); !ok {
		t.Error("a plain string is its own identity")
	}

	same := differentThing(
		map[string]any{"id": "FR-001", "requirement": "The system shall let a cashier take a sale"},
		map[string]any{"id": "FR-001", "requirement": "The system shall let a cashier take a sale quickly"})
	if same {
		t.Error("a reworded requirement is the same requirement")
	}
	other := differentThing(
		map[string]any{"id": "FR-001", "requirement": "The system shall track loyalty points"},
		map[string]any{"id": "FR-001", "requirement": "The system shall let a cashier take a sale"})
	if !other {
		t.Error("two unrelated requirements sharing an id are not the same thing")
	}
}

func TestGraphsRunEndToEnd(t *testing.T) {
	ctx := context.Background()
	svc := testService(t)

	project, session := hotelSession()
	if err := svc.Repo.CreateProject(ctx, *project); err != nil {
		t.Fatal(err)
	}
	plan := BuildOfflinePlan(project, session, "")
	st := &State{
		ProjectID: project.ID, Project: project, Session: session,
		Brief: project.RawIdea, Language: "English",
		Plan: plan, PlanMarkdown: RenderPlanMarkdown(plan, plan.AppName),
	}

	// No model is reachable in a test, so this exercises exactly the path a
	// machine with no Ollama takes.
	out, err := svc.RunGeneration(ctx, st)
	if err != nil {
		t.Fatalf("RunGeneration: %v", err)
	}
	if out.Document == nil {
		t.Fatal("no document")
	}
	if err := out.Document.Validate(); err != nil {
		t.Errorf("the composed document is invalid: %v", err)
	}
	if len(out.Diagrams) != 11 {
		t.Fatalf("diagrams = %d", len(out.Diagrams))
	}
	drawn := 0
	for _, d := range out.Diagrams {
		if d.MmdPath == "" {
			t.Errorf("%s has no source on disk", d.Kind)
		}
		if d.SvgPath != "" {
			drawn++
		}
	}
	if drawn != 11 {
		t.Errorf("%d of 11 diagrams were drawn without mermaid-cli", drawn)
	}
	if out.Document.BuilderHandoff == nil {
		t.Error("the builder handoff is missing")
	}
	if out.Document.StandardsProfile == nil {
		t.Error("the standards profile is missing")
	}

	// The files really exist.
	if _, err := os.Stat(filepath.Join(svc.diagramsDir(project.ID), "erd.svg")); err != nil {
		t.Errorf("erd.svg: %v", err)
	}
	if err := svc.SaveSRS(project.ID, "1.0.0", out.Document); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(svc.LatestSRSPath(project.ID)); err != nil {
		t.Errorf("srs_latest.json: %v", err)
	}
	snapshot := svc.SnapshotDiagrams(project.ID, "1.0.0", out.Diagrams)
	if !strings.Contains(snapshot[0].MmdPath, "v1.0.0") {
		t.Errorf("the snapshot did not take: %q", snapshot[0].MmdPath)
	}

	before := len(out.Document.FunctionalRequirements)
	out.CustomizationPrompt = "add a loyalty points programme"
	edited, err := svc.RunCustomization(ctx, out)
	if err != nil {
		t.Fatalf("RunCustomization: %v", err)
	}
	if len(edited.DiffSummary) == 0 {
		t.Error("an edit must report what it changed")
	}
	if len(edited.Document.FunctionalRequirements) <= before {
		t.Error("the edit added nothing")
	}
	if len(edited.Document.EffectivePlan) == 0 {
		t.Error("the effective plan should be refreshed after an edit")
	}
	if err := edited.Document.Validate(); err != nil {
		t.Errorf("the edited document is invalid: %v", err)
	}
}
