package srs

import (
	"math"
	"strings"
	"time"
)

// The specification as the customer receives it. The order is the one
// ISO/IEC/IEEE 29148 expects, with the approved plan restated first so a
// reader can see for themselves that the specification does not exceed it.

// PDF writes the SRS to a file. It renders the whole document twice: the first
// pass discovers which page each heading lands on, the second inserts a
// contents page of a known length and shifts every number by it.
func PDF(doc *Document, path, status string) error {
	if status == "" {
		status = "Draft"
	}
	first := newWriter(firstNonEmpty(doc.ProjectName, "Project"), firstNonEmpty(doc.Version, "1.0.0"))
	first.compose(doc, status, nil)

	// The measuring pass already spent one page on an empty contents, so the
	// shift is what the real contents adds on top of that.
	shift := tocPageCount(first.entries) - 1

	final := newWriter(firstNonEmpty(doc.ProjectName, "Project"), firstNonEmpty(doc.Version, "1.0.0"))
	contents := make([]tocEntry, len(first.entries))
	for i, e := range first.entries {
		contents[i] = tocEntry{e.Level, e.Text, e.Page + shift}
	}
	final.compose(doc, status, contents)
	return final.pdf.OutputFileAndClose(path)
}

// compose writes the whole document. `contents` is nil on the measuring pass.
func (w *writer) compose(doc *Document, status string, contents []tocEntry) {
	w.cover(doc, status)
	w.contentsPage(contents)
	w.documentControl(doc, status)

	plan := planFromDoc(doc.ApprovedPlan)
	authOn := doc.Auth.LoginRequired

	n := w.heading1("Introduction")
	w.heading2(itoa(n) + ".1 Purpose")
	w.body("This document specifies the requirements for " + doc.ProjectName + ". " +
		doc.AppSummary.BusinessGoal)
	w.heading2(itoa(n) + ".2 Scope")
	w.body(doc.AppSummary.ShortDescription)
	w.heading2(itoa(n) + ".3 Definitions, Acronyms and Abbreviations")
	definitions := "SRS — Software Requirements Specification; FR — Functional Requirement; " +
		"NFR — Non-Functional Requirement; "
	if authOn {
		definitions += "RBAC — Role-Based Access Control; "
	}
	w.body(definitions + "RTM — Requirement Traceability Matrix; PWA — Progressive Web App.")

	std := Knowledge().Standards
	w.heading2(itoa(n) + ".4 References")
	w.body(std.SRSStandard + ", " + std.SRSStandardTitle + "; " + std.UMLStandard +
		", Unified Modeling Language; " + std.BPMNStandard + ", Business Process Model and " +
		"Notation. ER diagrams use Crow's Foot cardinality notation and data-flow diagrams " +
		"use conventional Yourdon/DeMarco-style notation.")
	w.heading2(itoa(n) + ".5 Overview")
	if len(doc.ApprovedPlan) > 0 {
		w.body("Section 2 restates the plan the customer approved — this specification " +
			"describes that plan and nothing beyond it. The sections that follow give an " +
			"overall description, the detailed requirements, and then data design, UI/UX, " +
			"risks, acceptance criteria and diagrams.")
		w.newPage()
		w.planSection(plan)
	} else {
		w.body("Section 2 gives an overall description; Section 3 specifies detailed " +
			"requirements; the rest cover data design, access control, UI/UX, risks, " +
			"acceptance criteria, and diagrams.")
	}

	w.overallDescription(doc, authOn)
	w.newPage()
	w.systemRequirements(doc)
	w.newPage()
	w.dataDesign(doc)
	w.accessAndDesign(doc, authOn)
	w.newPage()
	w.risksAndAcceptance(doc)
	w.newPage()
	w.diagramAppendix(doc)
}

// --- the front matter ---------------------------------------------------------------------

func (w *writer) cover(doc *Document, status string) {
	w.chrome = false
	w.newPage()
	w.y = 3.5 * cm

	w.para("AgentForge Studio", style{Size: 12, Leading: 16, Bold: true, Colour: pdfPrimary, Centre: true})
	w.para("Requirements Engineering Report", style{Size: 10, Leading: 14, Colour: pdfMuted, Centre: true})
	w.spacer(1.4 * cm)
	w.para(firstNonEmpty(doc.ProjectName, "Project"),
		style{Size: 26, Leading: 30, Colour: pdfInk, Centre: true})
	w.spacer(0.4 * cm)
	w.para("Software Requirements Specification",
		style{Size: 13, Leading: 18, Colour: pdfMuted, Centre: true})
	std := Knowledge().Standards
	w.para(std.SRSStandard+" aligned format",
		style{Size: 10, Leading: 14, Colour: pdfMuted, Centre: true})
	w.spacer(1.6 * cm)

	rows := [][]cell{
		{{Text: "Version", Style: style{Size: 10, Leading: 14, Colour: pdfMuted}},
			{Text: firstNonEmpty(doc.Version, "1.0.0"), Style: style{Size: 10, Leading: 14, Bold: true, Colour: pdfInk}}},
		{{Text: "Status", Style: style{Size: 10, Leading: 14, Colour: pdfMuted}},
			{Text: status, Style: style{Size: 10, Leading: 14, Bold: true, Colour: pdfInk}}},
		{{Text: "Generated", Style: style{Size: 10, Leading: 14, Colour: pdfMuted}},
			{Text: time.Now().UTC().Format("2006-01-02"), Style: style{Size: 10, Leading: 14, Bold: true, Colour: pdfInk}}},
		{{Text: "System Category", Style: style{Size: 10, Leading: 14, Colour: pdfMuted}},
			{Text: truncate(doc.SystemCategory, 60), Style: style{Size: 10, Leading: 14, Bold: true, Colour: pdfInk}}},
		{{Text: "Architecture", Style: style{Size: 10, Leading: 14, Colour: pdfMuted}},
			{Text: truncate(doc.AppType.PrimaryType, 60), Style: style{Size: 10, Leading: 14, Bold: true, Colour: pdfInk}}},
		{{Text: "Language", Style: style{Size: 10, Leading: 14, Colour: pdfMuted}},
			{Text: firstNonEmpty(doc.DocumentLanguage, "English"), Style: style{Size: 10, Leading: 14, Bold: true, Colour: pdfInk}}},
		{{Text: "Requirements Standard", Style: style{Size: 10, Leading: 14, Colour: pdfMuted}},
			{Text: std.SRSStandard, Style: style{Size: 10, Leading: 14, Bold: true, Colour: pdfInk}}},
		{{Text: "Diagram Standards", Style: style{Size: 10, Leading: 14, Colour: pdfMuted}},
			{Text: std.UMLStandard + "; " + std.BPMNStandard + "; " + std.ERDNotation + "; DFD",
				Style: style{Size: 10, Leading: 14, Bold: true, Colour: pdfInk}}},
	}
	w.plainTable(rows, []float64{5 * cm, 9 * cm})
	w.chrome = true
}

// plainTable is the cover's key/value list: no grid, one hairline per row.
func (w *writer) plainTable(rows [][]cell, widths []float64) {
	x0 := marginLeft + (w.width-widths[0]-widths[1])/2
	for _, row := range rows {
		w.ensure(22)
		x := x0
		for i, c := range row {
			w.setFont(c.Style)
			w.pdf.Text(x+2, w.y+12, w.out(c.Text))
			x += widths[i]
		}
		w.y += 20
		w.pdf.SetDrawColor(pdfLine.R, pdfLine.G, pdfLine.B)
		w.pdf.SetLineWidth(0.4)
		w.pdf.Line(x0, w.y-4, x0+widths[0]+widths[1], w.y-4)
	}
}

// tocLeading is how tall one contents line is. tocPageCount depends on this
// matching contentsPage exactly: if the two disagree the whole index is off by
// however many pages the contents itself takes.
func tocLeading(level int) float64 {
	if level > 0 {
		return 14
	}
	return 18
}

// tocPageCount walks the entries the same way contentsPage draws them, so the
// page shift applied to every recorded number is exact rather than estimated.
func tocPageCount(entries []tocEntry) int {
	pages := 1
	y := marginTop + tocTitleHeight
	for _, e := range entries {
		leading := tocLeading(e.Level)
		if y+leading > pageHeight-marginBot {
			pages++
			y = marginTop
		}
		y += leading
	}
	return pages
}

const tocTitleHeight = 32.0

func (w *writer) contentsPage(contents []tocEntry) {
	w.newPage()
	w.setFont(style{Size: 16, Bold: true, Colour: pdfPrimary})
	w.pdf.Text(marginLeft, w.y+16, w.out("Table of Contents"))
	w.y += tocTitleHeight

	for _, e := range contents {
		s := style{Size: 10.5, Leading: tocLeading(e.Level), Bold: true, Colour: pdfInk}
		if e.Level > 0 {
			s = style{Size: 9, Leading: tocLeading(e.Level), Colour: pdfMuted, Indent: 16}
		}
		w.ensure(s.Leading)
		w.setFont(s)
		page := itoa(e.Page)
		x := marginLeft + s.Indent
		right := marginLeft + w.width
		label := w.out(e.Text)
		// Leave room for the page number, and a gap before it.
		for w.pdf.GetStringWidth(label) > w.width-s.Indent-40 && len(label) > 4 {
			label = label[:len(label)-1]
		}
		w.pdf.Text(x, w.y+s.Size, label)
		w.pdf.Text(right-w.pdf.GetStringWidth(page), w.y+s.Size, page)
		w.y += s.Leading
	}
	w.newPage()
}

func (w *writer) documentControl(doc *Document, status string) {
	std := Knowledge().Standards
	w.frontHeading("Document Control")

	control := doc.DocumentControl
	value := func(key, fallback string) string {
		if control == nil {
			return fallback
		}
		return firstNonEmpty(firstText(control[key]), fallback)
	}
	w.table([][]cell{
		{boldCell("Document ID"), plainCell(value("document_id", "SRS"))},
		{boldCell("Version"), plainCell(value("version", firstNonEmpty(doc.Version, "1.0.0")))},
		{boldCell("Status"), plainCell(status)},
		{boldCell("Prepared Date"), plainCell(value("prepared_date", time.Now().UTC().Format("2006-01-02")))},
		{boldCell("Document Owner"), plainCell(value("document_owner", "Project Stakeholders"))},
		{boldCell("Standard Profile"), plainCell(std.SRSStandard)},
	}, []float64{4.2 * cm, w.width - 4.2*cm}, false)

	w.small("This SRS is structured and quality-checked against " + std.SRSStandard +
		" (" + std.SRSStandardTitle + "). The profile is an engineering alignment, not a " +
		"third-party certification. Project-specific approval remains the responsibility " +
		"of the organisation and stakeholders.")
	w.spacer(6)

	w.heading2("Revision History")
	rows := [][]cell{{headerCell("Version"), headerCell("Date"), headerCell("Description")}}
	for i, rev := range doc.RevisionHistory {
		if i >= 12 {
			break
		}
		rows = append(rows, []cell{
			plainCell(firstText(rev["version"])), plainCell(firstText(rev["date"])),
			plainCell(firstText(rev["description"]))})
	}
	w.table(rows, []float64{2.4 * cm, 3 * cm, w.width - 5.4*cm}, true)

	if len(doc.ApprovalRecord) == 0 {
		w.small("Formal stakeholder approval has not yet been recorded in this generated baseline.")
		w.newPage()
		return
	}
	w.heading2("Approval Record")
	rows = [][]cell{{headerCell("Role"), headerCell("Name"), headerCell("Date"), headerCell("Status")}}
	for i, a := range doc.ApprovalRecord {
		if i >= 12 {
			break
		}
		rows = append(rows, []cell{plainCell(firstText(a["role"])), plainCell(firstText(a["name"])),
			plainCell(firstText(a["date"])), plainCell(firstText(a["status"]))})
	}
	w.table(rows, []float64{3 * cm, 4 * cm, 3 * cm, w.width - 10*cm}, true)
	w.newPage()
}

// --- the approved plan ----------------------------------------------------------------------

// planSection restates what the customer signed, so the specification can be
// checked against it without leaving the document.
func (w *writer) planSection(plan *Plan) {
	n := itoa(w.heading1("The Approved Plan"))
	w.small("This is what the customer read and approved. Everything that follows " +
		"specifies this plan, and nothing beyond it.")

	w.heading2(n + ".1 What we are building")
	w.body(plan.ProductIntent)

	if notes := strings.TrimSpace(plan.CustomerNotes); notes != "" {
		w.heading2(n + ".1b In the customer's own words")
		w.small("Written at the end of the interview, unprompted, when asked what else " +
			"the system should show:")
		w.body(notes)
	}

	if len(plan.Users) > 0 {
		w.heading2(n + ".2 Who uses it")
		rows := [][]cell{{headerCell("Role"), headerCell("What they can do")}}
		for _, u := range plan.Users {
			rows = append(rows, []cell{boldCell(u.Role), plainCell(strings.Join(u.CanDo, "; "))})
		}
		w.table(rows, []float64{4 * cm, w.width - 4*cm}, true)
	}

	if len(plan.Screens) > 0 {
		w.heading2(n + ".3 Screens")
		rows := [][]cell{{headerCell("Screen"), headerCell("What it is for"), headerCell("Who sees it")}}
		for _, s := range plan.Screens {
			rows = append(rows, []cell{boldCell(s.Name), plainCell(s.Purpose),
				plainCell(orText(strings.Join(s.Who, ", "), "Everyone"))})
		}
		w.table(rows, []float64{4 * cm, w.width - 8*cm, 4 * cm}, true)
	}

	if len(plan.Records) > 0 {
		w.heading2(n + ".4 What it keeps track of")
		rows := [][]cell{{headerCell("Record"), headerCell("What is kept")}}
		for _, r := range plan.Records {
			rows = append(rows, []cell{boldCell(r.Name),
				plainCell(orText(strings.Join(r.Keeps, ", "), "—"))})
		}
		w.table(rows, []float64{4 * cm, w.width - 4*cm}, true)
	}

	if len(plan.Workflows) > 0 {
		w.heading2(n + ".5 How the work flows")
		for _, flow := range plan.Workflows {
			w.para(firstNonEmpty(flow.Name, "Workflow"), styleCellB)
			for i, step := range flow.Steps {
				w.body(itoa(i+1) + ". " + step)
			}
			w.spacer(4)
		}
	}
	if len(plan.Features) > 0 {
		w.heading2(n + ".6 What it does")
		for _, f := range plan.Features {
			w.bullet(f)
		}
	}
	if plan.LookAndFeel != "" {
		w.heading2(n + ".7 Look and feel")
		w.body(plan.LookAndFeel)
	}
	if len(plan.Assumptions) > 0 {
		w.heading2(n + ".8 What we assumed")
		for _, a := range plan.Assumptions {
			w.bullet(a)
		}
	}
	if len(plan.OpenQuestions) > 0 {
		w.heading2(n + ".9 Still to settle")
		for _, q := range plan.OpenQuestions {
			mark := ""
			if q.Required {
				mark = " (needed before build)"
			}
			w.bullet(q.Question + mark)
		}
	}
}

// --- the body ----------------------------------------------------------------------------

func (w *writer) overallDescription(doc *Document, authOn bool) {
	n := itoa(w.heading1("Overall Description"))
	w.heading2(n + ".1 Product Perspective")
	kind := ""
	if doc.AppType.Key != "" {
		kind = " (" + doc.AppType.Key + ")"
	}
	w.body("The product is a " + firstNonEmpty(doc.AppType.PrimaryType, "web application") + kind + ".")

	w.heading2(n + ".2 Product Functions")
	for _, module := range doc.MainModules {
		w.bullet(module)
	}

	w.heading2(n + ".3 User Characteristics")
	if authOn || len(doc.Roles) > 1 {
		rows := [][]cell{{headerCell("Role"), headerCell("Description")}}
		for _, r := range doc.Roles {
			rows = append(rows, []cell{boldCell(r.RoleName), plainCell(r.Description)})
		}
		w.table(rows, []float64{4.5 * cm, w.width - 4.5*cm}, true)
	} else {
		w.body("There are no accounts and no roles. Anyone who opens the application " +
			"can use all of it.")
	}

	w.heading2(n + ".4 Constraints")
	for _, c := range doc.Constraints {
		w.bullet(c)
	}
	w.heading2(n + ".5 Assumptions and Dependencies")
	for _, a := range doc.Assumptions {
		w.bullet(a)
	}
}

func (w *writer) systemRequirements(doc *Document) {
	n := itoa(w.heading1("System Requirements"))

	w.heading2(n + ".1 Functional Requirements")
	rows := [][]cell{{headerCell("ID"), headerCell("Module"), headerCell("Requirement"),
		headerCell("Priority"), headerCell("Verification")}}
	for _, fr := range doc.FunctionalRequirements {
		rows = append(rows, []cell{boldCell(fr.ID), plainCell(fr.Module), plainCell(fr.Requirement),
			plainCell(fr.Priority), smallCell(firstNonEmpty(fr.VerificationMethod, "Functional Test"))})
	}
	w.table(rows, []float64{1.7 * cm, 2.6 * cm, w.width - 8.45*cm, 1.7 * cm, 2.45 * cm}, true)

	w.heading2(n + ".2 Non-Functional Requirements")
	rows = [][]cell{{headerCell("ID"), headerCell("Category"), headerCell("Requirement"),
		headerCell("Verification")}}
	for _, nfr := range doc.NonFunctionalRequirements {
		rows = append(rows, []cell{boldCell(nfr.ID), plainCell(nfr.Category), plainCell(nfr.Requirement),
			smallCell(firstNonEmpty(nfr.VerificationMethod, "Test / Analysis"))})
	}
	w.table(rows, []float64{1.8 * cm, 2.6 * cm, w.width - 7*cm, 2.6 * cm}, true)

	w.heading2(n + ".3 Security Requirements")
	for _, s := range doc.SecurityRequirements {
		w.bullet(s)
	}

	w.heading2(n + ".4 External Interface & Integration Requirements")
	if len(doc.IntegrationRequirements) == 0 {
		w.body("No external integrations required for the initial release.")
	} else {
		rows = [][]cell{{headerCell("Integration"), headerCell("Type"), headerCell("Description")}}
		for _, ig := range doc.IntegrationRequirements {
			rows = append(rows, []cell{boldCell(ig.Name), plainCell(ig.Type), plainCell(ig.Description)})
		}
		w.table(rows, []float64{4 * cm, 2.6 * cm, w.width - 6.6*cm}, true)
	}

	w.heading2(n + ".5 Business Workflows")
	for _, wf := range doc.BusinessWorkflows {
		w.para(firstNonEmpty(wf.WorkflowName, "Workflow"), styleCellB)
		for i, step := range wf.Steps {
			w.body(itoa(i+1) + ". " + step)
		}
		w.spacer(4)
	}

	w.heading2(n + ".6 Requirement Traceability Matrix")
	rows = [][]cell{{headerCell("Req"), headerCell("Source / Module"), headerCell("Design/Data"),
		headerCell("Verification"), headerCell("Test")}}
	for i, tr := range doc.Traceability {
		if i >= 60 {
			break
		}
		design := append(limit(tr.Pages, 2), limit(tr.Tables, 2)...)
		rows = append(rows, []cell{boldCell(tr.RequirementID),
			smallCell(firstNonEmpty(tr.Source, tr.Module, "Approved SRS")),
			smallCell(strings.Join(design, ", ")),
			smallCell(firstNonEmpty(tr.VerificationMethod, "Functional Test")),
			plainCell(tr.TestCase)})
	}
	w.table(rows, []float64{1.8 * cm, 3.2 * cm, w.width - 10.05*cm, 2.8 * cm, 2.25 * cm}, true)

	w.heading2(n + ".7 Requirements Quality Review")
	var review []any
	if doc.RequirementsQualityRev != nil {
		review, _ = doc.RequirementsQualityRev["items_needing_human_review"].([]any)
	}
	if len(review) == 0 {
		w.body("No automated wording/verification lint warnings were found. Human " +
			"stakeholder review is still required for baseline approval.")
		return
	}
	w.body("The following requirements contain conservative wording/verification lint " +
		"warnings and should be reviewed before formal baseline approval.")
	rows = [][]cell{{headerCell("Requirement"), headerCell("Review warning")}}
	for i, item := range review {
		if i >= 30 {
			break
		}
		row, _ := item.(map[string]any)
		rows = append(rows, []cell{boldCell(firstText(row["requirement_id"])),
			plainCell(strings.Join(stringList(row["warnings"]), "; "))})
	}
	w.table(rows, []float64{3 * cm, w.width - 3*cm}, true)
}

func (w *writer) dataDesign(doc *Document) {
	n := itoa(w.heading1("Data Requirements and Database Design"))
	for _, t := range doc.DatabaseDesign.Tables {
		w.para(t.TableName, styleCellB)
		if t.Description != "" {
			w.small(t.Description)
		}
		rows := [][]cell{{headerCell("Field"), headerCell("Type"), headerCell("Key/Notes")}}
		for _, f := range t.Fields {
			var notes []string
			if f.PrimaryKey {
				notes = append(notes, "PK")
			}
			if f.Type == "foreign_key" {
				notes = append(notes, "FK→"+f.References)
			}
			if f.Unique {
				notes = append(notes, "unique")
			}
			if len(f.Values) > 0 {
				var values []string
				for _, v := range limitAny(f.Values, 5) {
					values = append(values, firstText(v))
				}
				notes = append(notes, "enum: "+strings.Join(values, ", "))
			}
			if text := firstText(f.Default); text != "" {
				notes = append(notes, "default="+text)
			}
			rows = append(rows, []cell{plainCell(f.Name), plainCell(f.Type),
				smallCell(strings.Join(notes, ", "))})
		}
		w.table(rows, []float64{4 * cm, 3 * cm, w.width - 7*cm}, true)
		w.spacer(6)
	}

	if len(doc.DatabaseDesign.Relationships) == 0 {
		return
	}
	w.heading2(n + ".1 Relationships")
	rows := [][]cell{{headerCell("From"), headerCell("To"), headerCell("Type"), headerCell("Description")}}
	for _, rel := range doc.DatabaseDesign.Relationships {
		rows = append(rows, []cell{plainCell(rel.From), plainCell(rel.To),
			plainCell(rel.Type), plainCell(rel.Description)})
	}
	w.table(rows, []float64{3.4 * cm, 3.4 * cm, 2.6 * cm, w.width - 9.4*cm}, true)
}

func (w *writer) accessAndDesign(doc *Document, authOn bool) {
	if authOn && len(doc.RoleAccessMatrix) > 0 {
		w.newPage()
		w.heading1("Role Access Matrix")
		rows := [][]cell{{headerCell("Role"), headerCell("Allowed Pages"), headerCell("Allowed Functions")}}
		for _, m := range doc.RoleAccessMatrix {
			rows = append(rows, []cell{boldCell(m.Role),
				plainCell(strings.Join(m.AllowedPages, ", ")),
				plainCell(strings.Join(m.AllowedFunctions, ", "))})
		}
		w.table(rows, []float64{3 * cm, 5 * cm, w.width - 8*cm}, true)
	}

	w.heading1("UI/UX Requirements")
	for _, key := range []string{"design_style", "theme", "dashboard_layout"} {
		if value := firstText(doc.UIUX[key]); value != "" {
			w.body(titleCase(strings.ReplaceAll(key, "_", " ")) + ": " + value)
		}
	}
	if components := stringList(doc.UIUX["required_components"]); len(components) > 0 {
		w.body("Components: " + strings.Join(components, ", "))
	}
	if len(doc.Branding) == 0 {
		return
	}
	w.body("Theme: " + firstText(doc.Branding["theme"], "light") +
		" · Palette: " + firstText(doc.Branding["palette"]) +
		" (" + firstText(doc.Branding["primary_color"]) + ")")
	if required, _ := doc.Branding["logo_required"].(bool); required {
		w.body("Logo: to be generated. Image brief:")
		w.small(firstText(doc.Branding["logo_image_prompt"]))
		return
	}
	if firstText(doc.Branding["logo_source"]) == "upload" {
		w.body("Logo: supplied by the customer.")
	}
}

func (w *writer) risksAndAcceptance(doc *Document) {
	w.heading1("Risks and Priorities")
	for _, risk := range doc.RiskPriority {
		w.badge(firstNonEmpty(risk.Severity, "Medium")+" Risk", severityColour(risk.Severity))
		w.body(risk.Risk + " — " + risk.Reason)
		w.small("Mitigation: " + risk.Mitigation)
		w.spacer(6)
	}
	w.heading1("Acceptance Criteria")
	for _, ac := range doc.AcceptanceCriteria {
		w.body("☑ " + ac.Criterion)
	}
}

func severityColour(severity string) colour {
	low := strings.ToLower(severity)
	switch {
	case strings.Contains(low, "high"):
		return pdfRed
	case strings.Contains(low, "med"):
		return pdfOrange
	}
	return pdfGreen
}

// --- the appendix -----------------------------------------------------------------------

func (w *writer) diagramAppendix(doc *Document) {
	section := itoa(w.heading1("Appendix — Diagrams"))
	diagrams := doc.Diagrams
	if len(diagrams) == 0 {
		diagrams = BuildDiagrams(doc, nil)
	}
	w.small("This appendix contains " + itoa(len(diagrams)) + " specification-derived " +
		"diagrams. All diagrams use deterministic native vector rendering for the canonical " +
		"PDF/web output; Mermaid sources remain available as editable previews. No semantic " +
		"relationship is added unless it can be supported by the SRS data.")

	notation := Knowledge().Standards.DiagramNotation
	for i, d := range diagrams {
		if i > 0 {
			w.newPage()
		}
		w.heading2(section + "." + itoa(i+1) + " " + firstNonEmpty(d.Title, d.Kind, "Diagram"))
		w.small("Notation: " + firstNonEmpty(d.Standard, "Specification-derived notation") +
			" · semantic source: SRS")
		if !d.Applicable {
			w.small("Applicability: Not applicable — " + firstNonEmpty(d.ApplicabilityNote,
				"the specification does not provide enough semantics for this diagram."))
		}
		w.spacer(4)

		legend := notation[d.Kind]
		// Keep the legend on the same page as the diagram it explains.
		reserve := 6.6*cm + float64(len(legend))*0.5*cm
		maxHeight := math.Max(7.5*cm, w.bottom()-marginTop-reserve)

		if drawing := Render(d.Kind, doc, w.width-18); drawing != nil {
			w.drawing(drawing, maxHeight)
		} else {
			// No renderer, so the editable source stands in rather than a gap.
			lines := strings.Split(d.Source, "\n")
			for j, line := range limit(lines, 46) {
				_ = j
				w.para(line, styleCode)
			}
		}

		if len(legend) == 0 {
			continue
		}
		w.spacer(6)
		w.para("Notation", style{Size: 8.5, Leading: 11, Bold: true, Colour: pdfMuted})
		for _, item := range legend {
			w.para("• "+item, styleSmall)
		}
	}
}

func limitAny(values []any, n int) []any {
	if len(values) > n {
		return values[:n]
	}
	return values
}
