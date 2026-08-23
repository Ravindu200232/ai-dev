package srs

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// pdfDoc is a specification complete enough to exercise every section.
func pdfDoc(t *testing.T) *Document {
	t.Helper()
	doc := diagramDoc(t)
	doc.DatabaseDesign.Tables[3].Fields = append(doc.DatabaseDesign.Tables[3].Fields,
		Field{Name: "status", Type: "enum", Values: []any{"draft", "paid"}})
	doc.FunctionalRequirements = append(doc.FunctionalRequirements, Requirement{
		ID: "FR-900", Requirement: "The system shall move a sale from draft to paid on payment."})
	doc.RiskPriority = []Risk{{ID: "RISK-001", Area: "Payments",
		Risk: "The card reader goes offline", Severity: "High",
		Reason: "Sales stop", Mitigation: "Allow a cash fallback"}}
	ApplyInternationalProfile(doc)
	doc.Diagrams = BuildDiagrams(doc, nil)
	return doc
}

func TestPDFIsAWholeDocument(t *testing.T) {
	doc := pdfDoc(t)
	path := filepath.Join(t.TempDir(), "srs.pdf")
	if err := PDF(doc, path, "Draft"); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(raw[:8]), "%PDF-") {
		t.Fatalf("not a PDF: %q", raw[:8])
	}
	if !strings.HasSuffix(strings.TrimSpace(string(raw)), "%%EOF") {
		t.Error("the file is not closed")
	}
	pages := len(regexp.MustCompile(`/Type\s*/Page[^s]`).FindAll(raw, -1))
	if pages < 15 {
		t.Errorf("a full SRS with 11 diagrams should run to many pages: %d", pages)
	}
	if len(raw) < 20000 {
		t.Errorf("suspiciously small: %d bytes", len(raw))
	}
}

// The contents page has to agree with where the headings actually landed.
func TestPDFContentsPageNumbersAreReal(t *testing.T) {
	doc := pdfDoc(t)
	name := firstNonEmpty(doc.ProjectName, "Project")
	version := firstNonEmpty(doc.Version, "1.0.0")

	first := newWriter(name, version)
	first.compose(doc, "Draft", nil)
	shift := tocPageCount(first.entries) - 1

	final := newWriter(name, version)
	contents := make([]tocEntry, len(first.entries))
	for i, e := range first.entries {
		contents[i] = tocEntry{e.Level, e.Text, e.Page + shift}
	}
	final.compose(doc, "Draft", contents)

	if len(final.entries) != len(first.entries) {
		t.Fatalf("the two passes disagree: %d vs %d", len(first.entries), len(final.entries))
	}
	for i := range contents {
		if final.entries[i].Text != contents[i].Text {
			t.Fatalf("heading %d moved: %q vs %q", i, contents[i].Text, final.entries[i].Text)
		}
		if final.entries[i].Page != contents[i].Page {
			t.Errorf("%q is listed on page %d but landed on %d",
				contents[i].Text, contents[i].Page, final.entries[i].Page)
		}
	}
	if final.entries[len(final.entries)-1].Page > final.pdf.PageCount() {
		t.Errorf("the contents lists page %d in a %d page document",
			final.entries[len(final.entries)-1].Page, final.pdf.PageCount())
	}
}

func TestPDFIndexesEverySection(t *testing.T) {
	doc := pdfDoc(t)
	w := newWriter("Corner Shop", "1.0.0")
	w.compose(doc, "Draft", nil)

	var headings []string
	for _, e := range w.entries {
		if e.Level == 0 {
			headings = append(headings, e.Text)
		}
	}
	blob := strings.Join(headings, "\n")
	for _, want := range []string{
		"Document Control", "1. Introduction", "2. The Approved Plan",
		"3. Overall Description", "4. System Requirements",
		"5. Data Requirements and Database Design", "Role Access Matrix",
		"UI/UX Requirements", "Risks and Priorities", "Acceptance Criteria",
		"Appendix — Diagrams",
	} {
		if !strings.Contains(blob, want) {
			t.Errorf("the contents is missing %q\ngot:\n%s", want, blob)
		}
	}
	// Every diagram gets its own numbered subsection in the appendix.
	count := 0
	for _, e := range w.entries {
		if e.Level == 1 && strings.Contains(e.Text, "Diagram") {
			count++
		}
	}
	if count < 11 {
		t.Errorf("the appendix indexed %d diagrams", count)
	}
}

func TestPDFWithoutAccountsSkipsTheAccessMatrix(t *testing.T) {
	plan := &Plan{
		AppName: "Plumb Co", ProductIntent: "A one page site for a plumber.",
		Users:    []PlanUser{{Role: "Visitor", CanDo: []string{"Read about the service"}}},
		Screens:  []Screen{{Name: "Home", Route: "/", Purpose: "The landing page", Who: []string{"Visitor"}}},
		Records:  []PlanRecord{{Name: "Enquiry", Keeps: []string{"Name", "Message"}}},
		Features: []string{"Contact form"},
	}
	project := NewProject("a one page site for a plumber", "English")
	doc := BuildSRSFromPlan(&project, plan, BuildPack("landing", project.RawIdea), &Session{}, "")
	ApplyInternationalProfile(doc)

	w := newWriter("Plumb Co", "1.0.0")
	w.compose(doc, "Draft", nil)
	for _, e := range w.entries {
		if e.Text == "Role Access Matrix" {
			t.Error("an app with no accounts has no access matrix to print")
		}
	}

	path := filepath.Join(t.TempDir(), "srs.pdf")
	if err := PDF(doc, path, "Draft"); err != nil {
		t.Fatal(err)
	}
}

func TestHexColour(t *testing.T) {
	if got := hexColour("#2F7D5B"); got != (colour{0x2F, 0x7D, 0x5B}) {
		t.Errorf("hexColour = %+v", got)
	}
	if got := hexColour("2F7D5B"); got != (colour{0x2F, 0x7D, 0x5B}) {
		t.Errorf("a missing hash should still parse: %+v", got)
	}
	for _, bad := range []string{"", "#12", "#GGGGGG"} {
		if got := hexColour(bad); got != (colour{0, 0, 0}) {
			t.Errorf("hexColour(%q) = %+v", bad, got)
		}
	}
}

func TestTOCPageCountMatchesTheDrawing(t *testing.T) {
	var entries []tocEntry
	for i := 0; i < 120; i++ {
		level := 1
		if i%5 == 0 {
			level = 0
		}
		entries = append(entries, tocEntry{level, "Heading " + itoa(i), 1})
	}
	pages := tocPageCount(entries)
	if pages < 2 {
		t.Fatalf("120 entries do not fit one page: %d", pages)
	}

	// Draw them and count how many pages the writer really used.
	w := newWriter("x", "1")
	w.chrome = true
	before := w.pdf.PageNo()
	w.contentsPage(entries)
	// contentsPage ends by starting the next page, so one is not the contents'.
	used := w.pdf.PageNo() - before - 1
	if used != pages {
		t.Errorf("tocPageCount said %d, the writer used %d", pages, used)
	}
}
