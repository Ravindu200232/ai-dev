"""Professional international-format SRS PDF generator (ReportLab)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from .diagram_draw import render_native
from .standards import SRS_STANDARD, SRS_STANDARD_TITLE, UML_STANDARD, BPMN_STANDARD, DIAGRAM_NOTATION

PRIMARY = colors.HexColor("#2563EB")
INK = colors.HexColor("#0F172A")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
SOFT = colors.HexColor("#F8FAFC")
GREEN = colors.HexColor("#16A34A")
ORANGE = colors.HexColor("#EA580C")
RED = colors.HexColor("#DC2626")

def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    s = {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=26, leading=30, textColor=INK),
        "subtitle": ParagraphStyle("subtitle", fontSize=13, leading=18, textColor=MUTED, alignment=TA_CENTER),
        "h1": ParagraphStyle("h1", fontSize=16, leading=20, spaceBefore=14, spaceAfter=8, textColor=PRIMARY, fontName="Helvetica-Bold"),
        "h2": ParagraphStyle("h2", fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=4, textColor=INK, fontName="Helvetica-Bold"),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=9.5, leading=13.5, textColor=INK),
        "small": ParagraphStyle("small", fontSize=8.5, leading=11, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontSize=8.5, leading=11, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontSize=8.5, leading=11, textColor=INK, fontName="Helvetica-Bold"),
        "code": ParagraphStyle("code", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#334155"), fontName="Courier"),
        "coverlabel": ParagraphStyle("coverlabel", fontSize=10, leading=14, textColor=MUTED, alignment=TA_CENTER),
    }
    return s

class _Doc(BaseDocTemplate):
    """Document template with header/footer + TOC notifications."""

    def __init__(self, path: str, meta: dict, **kw):
        super().__init__(path, pagesize=A4, topMargin=2.2 * cm, bottomMargin=1.8 * cm,
                         leftMargin=1.9 * cm, rightMargin=1.9 * cm, **kw)
        self.meta = meta
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="main")
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame], onPage=self._blank),
            PageTemplate(id="body", frames=[frame], onPage=self._chrome),
        ])
        self._headings: list[tuple[int, str, int]] = []

    def _blank(self, canvas, doc):
        pass

    def _chrome(self, canvas, doc):
        canvas.saveState()

        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(self.leftMargin, A4[1] - 1.5 * cm, A4[0] - self.rightMargin, A4[1] - 1.5 * cm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(self.leftMargin, A4[1] - 1.35 * cm, self.meta.get("project_name", "SRS")[:70])
        canvas.drawRightString(A4[0] - self.rightMargin, A4[1] - 1.35 * cm,
                               f"SRS v{self.meta.get('version', '1.0.0')}")

        canvas.line(self.leftMargin, 1.4 * cm, A4[0] - self.rightMargin, 1.4 * cm)
        canvas.drawString(self.leftMargin, 1.1 * cm, f"AgentForge Studio · {SRS_STANDARD}-aligned")
        canvas.drawRightString(A4[0] - self.rightMargin, 1.1 * cm, f"Page {doc.page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            style = flowable.style.name
            text = flowable.getPlainText()
            if style == "h1":
                self.notify("TOCEntry", (0, text, self.page))
            elif style == "h2":
                self.notify("TOCEntry", (1, text, self.page))

def _badge(text: str, color) -> Table:
    t = Table([[text]], colWidths=[len(text) * 5.6 + 14])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    return t

def _table(rows: list[list], widths: list[float], st: dict, header=True) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    t.setStyle(TableStyle(style))
    return t

def _P(text, st, key="cell"):
    return Paragraph(str(text) if text is not None else "", st[key])

def _sev_color(sev: str):
    s = (sev or "").lower()
    return RED if "high" in s else (ORANGE if "med" in s else GREEN)

def _plan_section(story: list, st: dict, W: float, h1, h2, plan: dict) -> None:
    """The plan the customer approved, restated as its own section."""
    n = h1("The Approved Plan")
    story.append(_P("This is what the customer read and approved. Everything that "
                    "follows specifies this plan, and nothing beyond it.", st, "small"))

    h2(f"{n}.1 What we are building")
    story.append(_P(plan.get("product_intent", ""), st, "body"))

    notes = str(plan.get("customer_notes") or "").strip()
    if notes:
        h2(f"{n}.1b In the customer's own words")
        story.append(_P("Written at the end of the interview, unprompted, when "
                        "asked what else the system should show:", st, "small"))
        for para in [p for p in notes.splitlines() if p.strip()]:
            story.append(_P(para, st, "body"))

    users = plan.get("users") or []
    if users:
        h2(f"{n}.2 Who uses it")
        rows = [[_P("Role", st, "cellb"), _P("What they can do", st, "cellb")]]
        for u in users:
            rows.append([_P(u.get("role", ""), st, "cellb"),
                         _P("; ".join(str(d) for d in (u.get("can_do") or [])), st)])
        story.append(_table(rows, [4 * cm, W - 4 * cm], st))

    screens = plan.get("screens") or []
    if screens:
        h2(f"{n}.3 Screens")
        rows = [[_P("Screen", st, "cellb"), _P("What it is for", st, "cellb"),
                 _P("Who sees it", st, "cellb")]]
        for s in screens:
            rows.append([_P(s.get("name", ""), st, "cellb"), _P(s.get("purpose", ""), st),
                         _P(", ".join(s.get("who") or []) or "Everyone", st)])
        story.append(_table(rows, [4 * cm, W - 8 * cm, 4 * cm], st))

    records = plan.get("records") or []
    if records:
        h2(f"{n}.4 What it keeps track of")
        rows = [[_P("Record", st, "cellb"), _P("What is kept", st, "cellb")]]
        for r in records:
            rows.append([_P(r.get("name", ""), st, "cellb"),
                         _P(", ".join(str(k) for k in (r.get("keeps") or [])) or "—", st)])
        story.append(_table(rows, [4 * cm, W - 4 * cm], st))

    workflows = plan.get("workflows") or []
    if workflows:
        h2(f"{n}.5 How the work flows")
        for w in workflows:
            story.append(_P(w.get("name", "Workflow"), st, "cellb"))
            for i, step in enumerate(w.get("steps") or [], 1):
                story.append(_P(f"{i}. {step}", st, "body"))
            story.append(Spacer(1, 4))

    features = plan.get("features") or []
    if features:
        h2(f"{n}.6 What it does")
        for f in features:
            story.append(_P(f"• {f}", st, "body"))

    if plan.get("look_and_feel"):
        h2(f"{n}.7 Look and feel")
        story.append(_P(plan["look_and_feel"], st, "body"))

    assumptions = plan.get("assumptions") or []
    if assumptions:
        h2(f"{n}.8 What we assumed")
        for a in assumptions:
            story.append(_P(f"• {a}", st, "body"))

    open_qs = plan.get("open_questions") or []
    if open_qs:
        h2(f"{n}.9 Still to settle")
        for q in open_qs:
            mark = " (needed before build)" if q.get("required") else ""
            story.append(_P(f"• {q.get('question', '')}{mark}", st, "body"))

def build_srs_pdf(srs: dict, out_path: str | Path, *, status: str = "Draft",
                  diagrams: list[dict] | None = None) -> Path:
    doc_data = srs.get("srs_document", srs)
    st = _styles()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = {"project_name": doc_data.get("project_name", "Project"),
            "version": doc_data.get("version", "1.0.0")}
    pdf = _Doc(str(out_path), meta)
    W = pdf.width
    story: list[Any] = []

    story += [Spacer(1, 3.5 * cm),
              Paragraph("AgentForge Studio", ParagraphStyle("brand", fontSize=12, textColor=PRIMARY, alignment=TA_CENTER, fontName="Helvetica-Bold")),
              Paragraph("Requirements Engineering Report", st["coverlabel"]),
              Spacer(1, 1.4 * cm),
              Paragraph(doc_data.get("project_name", "Project"), st["title"]),
              Spacer(1, 0.4 * cm),
              Paragraph("Software Requirements Specification", st["subtitle"]),
              Paragraph(f"{SRS_STANDARD} aligned format", st["coverlabel"]),
              Spacer(1, 1.6 * cm)]
    cover_tbl = Table([
        ["Version", doc_data.get("version", "1.0.0")],
        ["Status", status],
        ["Generated", date.today().isoformat()],
        ["System Category", doc_data.get("system_category", "")[:60]],
        ["Architecture", (doc_data.get("app_type", {}) or {}).get("primary_type", "")[:60]],
        ["Language", doc_data.get("document_language", "English")],
        ["Requirements Standard", SRS_STANDARD],
        ["Diagram Standards", f"{UML_STANDARD}; {BPMN_STANDARD}; Crow's Foot ERD; DFD"],
    ], colWidths=[5 * cm, 9 * cm])
    cover_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10), ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), INK), ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ]))
    story += [cover_tbl, PageBreak()]

    story.append(Paragraph("Table of Contents", st["h1"]))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("toc0", fontSize=10.5, leading=18, fontName="Helvetica-Bold", textColor=INK),
        ParagraphStyle("toc1", fontSize=9, leading=14, leftIndent=16, textColor=MUTED),
    ]
    story += [toc, PageBreak()]

    # Put document control before technical sections.
    story.append(Paragraph("Document Control", st["h1"]))
    dc = doc_data.get("document_control") or {}
    ctrl_rows = [
        [_P("Document ID", st, "cellb"), _P(dc.get("document_id", "SRS"), st)],
        [_P("Version", st, "cellb"), _P(dc.get("version", doc_data.get("version", "1.0.0")), st)],
        [_P("Status", st, "cellb"), _P(status, st)],
        [_P("Prepared Date", st, "cellb"), _P(dc.get("prepared_date", date.today().isoformat()), st)],
        [_P("Document Owner", st, "cellb"), _P(dc.get("document_owner", "Project Stakeholders"), st)],
        [_P("Standard Profile", st, "cellb"), _P(SRS_STANDARD, st)],
    ]
    story.append(_table(ctrl_rows, [4.2 * cm, W - 4.2 * cm], st, header=False))
    story.append(Spacer(1, 8))
    story.append(_P(
        f"This SRS is structured and quality-checked against {SRS_STANDARD} ({SRS_STANDARD_TITLE}). "
        "The profile is an engineering alignment, not a third-party certification. Project-specific "
        "approval remains the responsibility of the organisation and stakeholders.", st, "small"))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Revision History", st["h2"]))
    rrows = [[_P("Version", st, "cellb"), _P("Date", st, "cellb"), _P("Description", st, "cellb")]]
    for rev in (doc_data.get("revision_history") or [])[:12]:
        rrows.append([_P(rev.get("version"), st), _P(rev.get("date"), st), _P(rev.get("description"), st)])
    story.append(_table(rrows, [2.4 * cm, 3 * cm, W - 5.4 * cm], st))
    story.append(Spacer(1, 8))
    approvals = doc_data.get("approval_record") or []
    if approvals:
        story.append(Paragraph("Approval Record", st["h2"]))
        arows = [[_P("Role", st, "cellb"), _P("Name", st, "cellb"), _P("Date", st, "cellb"), _P("Status", st, "cellb")]]
        for a in approvals[:12]:
            arows.append([_P(a.get("role"), st), _P(a.get("name"), st), _P(a.get("date"), st), _P(a.get("status"), st)])
        story.append(_table(arows, [3 * cm, 4 * cm, 3 * cm, W - 10 * cm], st))
    else:
        story.append(_P("Formal stakeholder approval has not yet been recorded in this generated baseline.", st, "small"))
    story.append(PageBreak())

    section = {"n": 0}

    def h1(text) -> int:
        section["n"] += 1
        story.append(Paragraph(f"{section['n']}. {text}", st["h1"]))
        return section["n"]

    def h2(text):
        story.append(Paragraph(text, st["h2"]))

    def body(text):
        story.append(_P(text, st, "body"))

    plan = doc_data.get("approved_plan") or {}

    auth_on = bool((doc_data.get("authentication_requirement") or {}).get("login_required"))

    n = h1("Introduction")
    summ = doc_data.get("app_summary", {})
    h2(f"{n}.1 Purpose")
    body(f"This document specifies the requirements for {doc_data.get('project_name')}. "
         f"{summ.get('business_goal', '')}")
    h2(f"{n}.2 Scope")
    body(summ.get("short_description", ""))
    h2(f"{n}.3 Definitions, Acronyms and Abbreviations")
    body("SRS — Software Requirements Specification; FR — Functional Requirement; "
         "NFR — Non-Functional Requirement; "
         + ("RBAC — Role-Based Access Control; " if auth_on else "")
         + "RTM — Requirement Traceability Matrix; PWA — Progressive Web App.")
    h2(f"{n}.4 References")
    body(f"{SRS_STANDARD}, {SRS_STANDARD_TITLE}; {UML_STANDARD}, Unified Modeling Language; "
         f"{BPMN_STANDARD}, Business Process Model and Notation. ER diagrams use Crow's Foot "
         "cardinality notation and data-flow diagrams use conventional Yourdon/DeMarco-style notation.")
    h2(f"{n}.5 Overview")
    body(("Section 2 restates the plan the customer approved — this specification "
          "describes that plan and nothing beyond it. The sections that follow give "
          "an overall description, the detailed requirements, and then data design, "
          "UI/UX, risks, acceptance criteria and diagrams.") if plan else
         ("Section 2 gives an overall description; Section 3 specifies detailed "
          "requirements; the rest cover data design, access control, UI/UX, risks, "
          "acceptance criteria, and diagrams."))

    if plan:
        story.append(PageBreak())
        _plan_section(story, st, W, h1, h2, plan)

    n = h1("Overall Description")
    h2(f"{n}.1 Product Perspective")
    at = doc_data.get("app_type", {}) or {}
    kind = f" ({at['key']})" if at.get("key") else ""
    body(f"The product is a {at.get('primary_type', 'web application')}{kind}.")
    h2(f"{n}.2 Product Functions")
    for mod in doc_data.get("main_modules", []):
        story.append(_P(f"• {mod}", st, "body"))
    h2(f"{n}.3 User Characteristics")
    if auth_on or len(doc_data.get("roles", [])) > 1:
        rrows = [[_P("Role", st, "cellb"), _P("Description", st, "cellb")]]
        for r in doc_data.get("roles", []):
            rrows.append([_P(r.get("role_name"), st, "cellb"), _P(r.get("description"), st)])
        story.append(_table(rrows, [4.5 * cm, W - 4.5 * cm], st))
    else:
        body("There are no accounts and no roles. Anyone who opens the application "
             "can use all of it.")
    h2(f"{n}.4 Constraints")
    for c in doc_data.get("constraints", []):
        story.append(_P(f"• {c}", st, "body"))
    h2(f"{n}.5 Assumptions and Dependencies")
    for a in doc_data.get("assumptions", []):
        story.append(_P(f"• {a}", st, "body"))

    story.append(PageBreak())
    n = h1("System Requirements")
    h2(f"{n}.1 Functional Requirements")
    frows = [[_P("ID", st, "cellb"), _P("Module", st, "cellb"), _P("Requirement", st, "cellb"),
              _P("Priority", st, "cellb"), _P("Verification", st, "cellb")]]
    for fr in doc_data.get("functional_requirements", []):
        frows.append([_P(fr.get("id"), st, "cellb"), _P(fr.get("module"), st),
                      _P(fr.get("requirement"), st), _P(fr.get("priority"), st),
                      _P(fr.get("verification_method", "Functional Test"), st, "small")])
    story.append(_table(frows, [1.45 * cm, 2.4 * cm, W - 8.0 * cm, 1.7 * cm, 2.45 * cm], st))

    h2(f"{n}.2 Non-Functional Requirements")
    nrows = [[_P("ID", st, "cellb"), _P("Category", st, "cellb"), _P("Requirement", st, "cellb"), _P("Verification", st, "cellb")]]
    for nfr in doc_data.get("non_functional_requirements", []):
        nrows.append([_P(nfr.get("id"), st, "cellb"), _P(nfr.get("category"), st),
                      _P(nfr.get("requirement"), st), _P(nfr.get("verification_method", "Test / Analysis"), st, "small")])
    story.append(_table(nrows, [1.45 * cm, 2.5 * cm, W - 6.55 * cm, 2.6 * cm], st))

    h2(f"{n}.3 Security Requirements")
    for sreq in doc_data.get("security_requirements", []):
        story.append(_P(f"• {sreq}", st, "body"))

    h2(f"{n}.4 External Interface & Integration Requirements")
    if doc_data.get("integration_requirements"):
        irows = [[_P("Integration", st, "cellb"), _P("Type", st, "cellb"), _P("Description", st, "cellb")]]
        for ig in doc_data["integration_requirements"]:
            irows.append([_P(ig.get("name"), st, "cellb"), _P(ig.get("type"), st), _P(ig.get("description"), st)])
        story.append(_table(irows, [4 * cm, 2.6 * cm, W - 6.6 * cm], st))
    else:
        body("No external integrations required for the initial release.")

    h2(f"{n}.5 Business Workflows")
    for wf in doc_data.get("business_workflows", []):
        story.append(_P(wf.get("workflow_name", "Workflow"), st, "cellb"))
        for i, step in enumerate(wf.get("steps", []), 1):
            story.append(_P(f"{i}. {step}", st, "body"))
        story.append(Spacer(1, 4))

    h2(f"{n}.6 Requirement Traceability Matrix")
    trows = [[_P("Req", st, "cellb"), _P("Source / Module", st, "cellb"), _P("Design/Data", st, "cellb"),
              _P("Verification", st, "cellb"), _P("Test", st, "cellb")]]
    for tr in doc_data.get("requirement_traceability_matrix", [])[:60]:
        source = tr.get("source") or tr.get("module") or "Approved SRS"
        design = ", ".join((tr.get("pages") or [])[:2] + (tr.get("tables") or [])[:2])
        trows.append([_P(tr.get("requirement_id"), st, "cellb"), _P(source, st, "small"),
                      _P(design, st, "small"), _P(tr.get("verification_method", "Functional Test"), st, "small"),
                      _P(tr.get("test_case"), st)])
    story.append(_table(trows, [1.45 * cm, 3.2 * cm, W - 9.7 * cm, 2.8 * cm, 2.25 * cm], st))

    review = (doc_data.get("requirements_quality_review") or {}).get("items_needing_human_review") or []
    h2(f"{n}.7 Requirements Quality Review")
    if review:
        body("The following requirements contain conservative wording/verification lint warnings and should be reviewed before formal baseline approval.")
        qrows = [[_P("Requirement", st, "cellb"), _P("Review warning", st, "cellb")]]
        for item in review[:30]:
            qrows.append([_P(item.get("requirement_id"), st, "cellb"), _P("; ".join(item.get("warnings") or []), st)])
        story.append(_table(qrows, [3 * cm, W - 3 * cm], st))
    else:
        body("No automated wording/verification lint warnings were found. Human stakeholder review is still required for baseline approval.")

    story.append(PageBreak())
    n = h1("Data Requirements and Database Design")
    for t in doc_data.get("database_design", {}).get("tables", []):
        story.append(_P(t.get("table_name"), st, "cellb"))
        if t.get("description"):
            story.append(_P(t["description"], st, "small"))
        drows = [[_P("Field", st, "cellb"), _P("Type", st, "cellb"), _P("Key/Notes", st, "cellb")]]
        for fld in t.get("fields", []):
            notes = []
            if fld.get("primary_key"):
                notes.append("PK")
            if fld.get("type") == "foreign_key":
                notes.append(f"FK→{fld.get('references', '')}")
            if fld.get("unique"):
                notes.append("unique")
            if fld.get("values"):
                notes.append("enum: " + ", ".join(str(v) for v in fld["values"][:5]))
            if fld.get("default") is not None:
                notes.append(f"default={fld['default']}")
            drows.append([_P(fld.get("name"), st), _P(fld.get("type"), st), _P(", ".join(notes), st, "small")])
        story.append(_table(drows, [4 * cm, 3 * cm, W - 7 * cm], st))
        story.append(Spacer(1, 6))

    rels = doc_data.get("database_design", {}).get("relationships", [])
    if rels:
        h2(f"{n}.1 Relationships")
        relrows = [[_P("From", st, "cellb"), _P("To", st, "cellb"), _P("Type", st, "cellb"), _P("Description", st, "cellb")]]
        for rel in rels:
            relrows.append([_P(rel.get("from"), st), _P(rel.get("to"), st), _P(rel.get("type"), st), _P(rel.get("description"), st)])
        story.append(_table(relrows, [3.4 * cm, 3.4 * cm, 2.6 * cm, W - 9.4 * cm], st))

    matrix = doc_data.get("role_access_matrix") or []
    if auth_on and matrix:
        story.append(PageBreak())
        h1("Role Access Matrix")
        marows = [[_P("Role", st, "cellb"), _P("Allowed Pages", st, "cellb"), _P("Allowed Functions", st, "cellb")]]
        for m in matrix:
            marows.append([_P(m.get("role"), st, "cellb"),
                           _P(", ".join(m.get("allowed_pages", [])), st),
                           _P(", ".join(m.get("allowed_functions", [])), st)])
        story.append(_table(marows, [3 * cm, 5 * cm, W - 8 * cm], st))

    h1("UI/UX Requirements")
    ui = doc_data.get("ui_ux_requirements", {})
    for k in ("design_style", "theme", "dashboard_layout"):
        if ui.get(k):
            story.append(_P(f"<b>{k.replace('_', ' ').title()}:</b> {ui[k]}", st, "body"))
    if ui.get("required_components"):
        story.append(_P("<b>Components:</b> " + ", ".join(ui["required_components"]), st, "body"))
    brand = doc_data.get("branding") or {}
    if brand:
        story.append(_P(f"<b>Theme:</b> {brand.get('theme', 'light')} · "
                        f"<b>Palette:</b> {brand.get('palette', '')} "
                        f"({brand.get('primary_color', '')})", st, "body"))
        if brand.get("logo_required"):
            story.append(_P("<b>Logo:</b> to be generated. Image brief:", st, "body"))
            story.append(_P(brand.get("logo_image_prompt", ""), st, "small"))
        elif brand.get("logo_source") == "upload":
            story.append(_P("<b>Logo:</b> supplied by the customer.", st, "body"))

    story.append(PageBreak())
    h1("Risks and Priorities")
    for risk in doc_data.get("risk_priority", []):
        story.append(_badge(f"{risk.get('severity', 'Medium')} Risk", _sev_color(risk.get("severity"))))
        story.append(_P(f"<b>{risk.get('risk')}</b> — {risk.get('reason')}", st, "body"))
        story.append(_P(f"Mitigation: {risk.get('mitigation')}", st, "small"))
        story.append(Spacer(1, 6))

    h1("Acceptance Criteria")
    for ac in doc_data.get("acceptance_criteria", []):
        text = ac.get("criterion") if isinstance(ac, dict) else ac
        story.append(_P(f"☑ {text}", st, "body"))

    story.append(PageBreak())
    diagram_section = h1("Appendix — Diagrams")
    diagrams = diagrams if diagrams is not None else doc_data.get("diagrams", [])
    story.append(_P(f"This appendix contains {len(diagrams)} specification-derived diagrams. "
                    "All diagrams use deterministic native vector rendering for the canonical PDF/web output; "
                    "Mermaid sources remain available as editable previews. No semantic relationship is added "
                    "unless it can be supported by the SRS data.", st, "small"))
    base_max_img_h = pdf.height - 4 * cm
    for idx, d in enumerate(diagrams):
        if idx > 0:
            story.append(PageBreak())
        title = d.get("title", d.get("kind", "Diagram"))
        by = d.get("generated_by")
        story.append(Paragraph(f"{diagram_section}.{idx + 1} {title}", st["h2"]))
        std = d.get("standard") or "Specification-derived notation"
        if by:
            story.append(_P(f"Notation: {std} · semantic source: SRS · "
                            f"editable source: {'LLM-enriched' if by == 'llm' else 'deterministic'} Mermaid · "
                            f"canonical rendering: {d.get('rendered_by', 'native/vector fallback')}", st, "small"))
        else:
            story.append(_P(f"Notation: {std} · semantic source: SRS", st, "small"))
        if d.get("applicable") is False:
            story.append(_P("Applicability: Not applicable — " + str(d.get("applicability_note") or
                            "the specification does not provide enough semantics for this diagram."), st, "small"))
        story.append(Spacer(1, 6))

        notation = DIAGRAM_NOTATION.get(d.get("kind"), [])
        # Keep the notation legend on the same page.
        reserve = 6.6 * cm + len(notation) * 0.50 * cm
        max_img_h = min(base_max_img_h, max(7.5 * cm, pdf.height - reserve))

        placed = False

        png = d.get("png_path")
        if png and Path(png).exists():
            try:
                img = Image(png)
                ratio = img.imageHeight / float(img.imageWidth or 1)
                draw_w = min(W, 16.5 * cm)
                draw_h = draw_w * ratio
                if draw_h > max_img_h:
                    draw_h = max_img_h
                    draw_w = draw_h / (ratio or 1)
                img.drawWidth, img.drawHeight = draw_w, draw_h
                story.append(img)
                placed = True
            except Exception:  # noqa: BLE001
                placed = False

        if not placed:
            drawing = render_native(d.get("kind", ""), doc_data, W - 18)
            if drawing is not None:
                if drawing.height > max_img_h:
                    scale = max_img_h / drawing.height
                    drawing.scale(scale, scale)
                    drawing.width *= scale
                    drawing.height *= scale
                card = Table([[drawing]], colWidths=[W], hAlign="CENTER")
                card.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,-1), colors.white),
                    ("BOX", (0,0), (-1,-1), 0.6, LINE),
                    ("ALIGN", (0,0), (-1,-1), "CENTER"),
                    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                    ("LEFTPADDING", (0,0), (-1,-1), 8),
                    ("RIGHTPADDING", (0,0), (-1,-1), 8),
                    ("TOPPADDING", (0,0), (-1,-1), 8),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 8),
                ]))
                story.append(card)
                placed = True

        if not placed:
            src_lines = (d.get("source", "") or "").splitlines()[:46]
            box = [[Paragraph((ln.replace(" ", "&nbsp;") or "&nbsp;"), st["code"])] for ln in src_lines]
            tbl = Table(box or [[_P("(no diagram)", st, "small")]], colWidths=[W])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]))
            story.append(tbl)

        if notation:
            story.append(Spacer(1, 8))
            story.append(_P("<b>Notation</b>", st, "small"))
            for item in notation:
                story.append(_P(f"• {item}", st, "small"))

    pdf.multiBuild(story)
    return out_path
