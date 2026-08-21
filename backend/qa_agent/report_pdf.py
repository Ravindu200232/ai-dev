"""The testing tab as a document you can send to somebody."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

PRIMARY = colors.HexColor("#2563EB")
INK = colors.HexColor("#0F172A")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
SOFT = colors.HexColor("#F8FAFC")
GREEN = colors.HexColor("#16A34A")
ORANGE = colors.HexColor("#EA580C")
RED = colors.HexColor("#DC2626")

# A test file is evidence, not an appendix to skim.
SOURCE_CHARS = 9000
MAX_CASES = 400


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=26,
                                leading=30, textColor=INK),
        "subtitle": ParagraphStyle("subtitle", fontSize=13, leading=18,
                                   textColor=MUTED, alignment=TA_CENTER),
        "h1": ParagraphStyle("h1", fontSize=16, leading=20, spaceBefore=14,
                             spaceAfter=8, textColor=PRIMARY,
                             fontName="Helvetica-Bold"),
        "h2": ParagraphStyle("h2", fontSize=12.5, leading=16, spaceBefore=10,
                             spaceAfter=4, textColor=INK,
                             fontName="Helvetica-Bold"),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=9.5,
                               leading=13.5, textColor=INK),
        "small": ParagraphStyle("small", fontSize=8.5, leading=11, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontSize=8.5, leading=11, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontSize=8.5, leading=11, textColor=INK,
                                fontName="Helvetica-Bold"),
        "code": ParagraphStyle("code", fontSize=7, leading=8.8,
                               textColor=colors.HexColor("#334155"),
                               fontName="Courier"),
        "coverlabel": ParagraphStyle("coverlabel", fontSize=10, leading=14,
                                     textColor=MUTED, alignment=TA_CENTER),
    }


class _Doc(BaseDocTemplate):
    def __init__(self, path: str, meta: dict, **kw):
        super().__init__(path, pagesize=A4, topMargin=2.2 * cm,
                         bottomMargin=1.8 * cm, leftMargin=1.9 * cm,
                         rightMargin=1.9 * cm, **kw)
        self.meta = meta
        frame = Frame(self.leftMargin, self.bottomMargin, self.width,
                      self.height, id="main")
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame], onPage=lambda c, d: None),
            PageTemplate(id="body", frames=[frame], onPage=self._chrome),
        ])

    def _chrome(self, canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(self.leftMargin, A4[1] - 1.5 * cm,
                    A4[0] - self.rightMargin, A4[1] - 1.5 * cm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(self.leftMargin, A4[1] - 1.35 * cm,
                          str(self.meta.get("project", ""))[:70])
        canvas.drawRightString(A4[0] - self.rightMargin, A4[1] - 1.35 * cm,
                               "Test Report")
        canvas.line(self.leftMargin, 1.4 * cm, A4[0] - self.rightMargin, 1.4 * cm)
        canvas.drawString(self.leftMargin, 1.1 * cm, "AgentForge Studio · QA")
        canvas.drawRightString(A4[0] - self.rightMargin, 1.1 * cm,
                               f"Page {doc.page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            if flowable.style.name == "h1":
                self.notify("TOCEntry", (0, flowable.getPlainText(), self.page))
            elif flowable.style.name == "h2":
                self.notify("TOCEntry", (1, flowable.getPlainText(), self.page))


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


def _table(rows: list[list], widths: list[float], header=True) -> Table:
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
        style += [("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
                  ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    return t


def _P(text, st, key="cell"):
    safe = ("" if text is None else str(text))
    safe = safe.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(safe, st[key])


def _code(text: str, st) -> Paragraph:
    safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace(" ", "&nbsp;").replace("\n", "<br/>"))
    return Paragraph(safe, st["code"])


def _score_colour(n) -> Any:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return MUTED
    return GREEN if n >= 90 else (ORANGE if n >= 50 else RED)


def _missing(story, st, what: str) -> None:
    """Say a stage did not run, rather than printing zeros for it."""
    story.append(Paragraph(f"This stage did not run, so there is nothing to "
                           f"report for {what}.", st["small"]))
    story.append(Spacer(1, 6))


def build_qa_pdf(qa: dict, out_path: str | Path, *, project: str = "") -> Path:
    """Render the whole testing tab into `out_path`."""
    qa = qa or {}
    project = project or qa.get("project") or "project"
    report = qa.get("report") or {}
    vitest = qa.get("vitest") or {}
    have = qa.get("have") or {}
    st = _styles()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = _Doc(str(out), {"project": project})
    W = doc.width
    story: list = []

    unit = report.get("unit") or {}
    suite = report.get("suite") or {}
    passed = unit.get("passed", vitest.get("numPassedTests"))
    failed = unit.get("failed", vitest.get("numFailedTests"))
    green = bool(failed == 0 and (passed or 0) > 0)

    story += [
        Spacer(1, 3.4 * cm),
        Paragraph("AgentForge Studio",
                  ParagraphStyle("brand", fontSize=12, textColor=PRIMARY,
                                 alignment=TA_CENTER, fontName="Helvetica-Bold")),
        Spacer(1, 0.5 * cm),
        Paragraph("Test Report", st["title"]),
        Spacer(1, 0.3 * cm),
        Paragraph(project, st["subtitle"]),
        Spacer(1, 1.2 * cm),
    ]
    verdict = ("All tests passing" if green
               else f"{failed or 0} failing" if failed
               else "No unit run recorded")
    story.append(Table([[_badge(verdict, GREEN if green else RED)]],
                       colWidths=[W], style=[("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    story.append(Spacer(1, 1.0 * cm))

    ran_at = report.get("at") or ""
    story.append(Table(
        [[_P("Unit tests", st, "cellb"), _P(f"{passed or 0} passed, {failed or 0} failed", st)],
         [_P("Suites", st, "cellb"), _P(vitest.get("numTotalTestSuites", "—"), st)],
         [_P("Repaired during the run", st, "cellb"),
          _P(f"{unit.get('fixed', 0)} test(s), {unit.get('code_fixes', 0)} code fix(es)", st)],
         [_P("Report written", st, "cellb"), _P(ran_at or "—", st)],
         [_P("Generated", st, "cellb"),
          _P(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), st)]],
        colWidths=[W * 0.38, W * 0.62]))
    story.append(PageBreak())

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("toc1", fontSize=10.5, leading=16, spaceBefore=4,
                       fontName="Helvetica-Bold", textColor=INK),
        ParagraphStyle("toc2", fontSize=9.5, leading=13, leftIndent=14,
                       textColor=MUTED),
    ]
    story += [Paragraph("Contents", st["h1"]), toc, PageBreak()]

    def h1(text):
        story.append(Paragraph(text, st["h1"]))

    def h2(text):
        story.append(Paragraph(text, st["h2"]))

    h1("1. Summary")
    story.append(Paragraph(
        "Every number here was measured on this project by the QA agent. A "
        "stage that did not run is named as not having run rather than shown "
        "as a zero.", st["body"]))
    story.append(Spacer(1, 6))

    perf_scores = (qa.get("performance") or {}).get("scores") or {}
    rows = [[_P("Stage", st, "cellb"), _P("Ran", st, "cellb"),
             _P("Result", st, "cellb")]]
    rows.append([_P("Unit tests", st), _P("yes" if unit.get("ran") else "no", st),
                 _P(f"{passed or 0} passed / {failed or 0} failed", st)])
    e2e = report.get("e2e") or {}
    rows.append([_P("Integration (end to end)", st),
                 _P("yes" if e2e.get("ran") else "no", st),
                 _P(f"{e2e.get('passed', 0)} passed / {e2e.get('failed', 0)} failed", st)])
    sec = report.get("security") or {}
    rows.append([_P("Security", st), _P("yes" if sec.get("ran") else "no", st),
                 _P(f"{len(sec.get('findings') or [])} finding(s)", st)])
    rows.append([_P("Performance", st),
                 _P("yes" if have.get("performance") or perf_scores else "no", st),
                 _P(", ".join(f"{k} {v}" for k, v in perf_scores.items()) or "—", st)])
    rows.append([_P("Runtime checks", st),
                 _P("yes" if report.get("runtime") is not None else "no", st),
                 _P(f"{len(report.get('runtime') or [])} issue(s)", st)])
    story.append(_table(rows, [W * 0.34, W * 0.13, W * 0.53]))
    story.append(Spacer(1, 8))

    if suite.get("quarantined") or suite.get("unresolved") or suite.get("suspects"):
        h2("Not everything is green")
        for label, items in (("Unresolved", suite.get("unresolved")),
                             ("Suspect", suite.get("suspects")),
                             ("Quarantined", suite.get("quarantined"))):
            if items:
                story.append(Paragraph(
                    f"<b>{label}:</b> " + ", ".join(str(x) for x in items),
                    st["body"]))
        story.append(Spacer(1, 6))

    story.append(PageBreak())
    h1("2. Unit tests")
    results = vitest.get("testResults") or []
    if not results:
        _missing(story, st, "unit tests")
    else:
        rows = [[_P("File", st, "cellb"), _P("Cases", st, "cellb"),
                 _P("Passed", st, "cellb"), _P("Failed", st, "cellb")]]
        for f in results:
            cases = f.get("assertionResults") or []
            ok = sum(1 for c in cases if c.get("status") == "passed")
            bad = sum(1 for c in cases if c.get("status") == "failed")
            rows.append([_P(_short(f.get("name", "")), st), _P(len(cases), st),
                         _P(ok, st), _P(bad, st)])
        story.append(_table(rows, [W * 0.58, W * 0.14, W * 0.14, W * 0.14]))
        story.append(Spacer(1, 10))

        h2("Every case")
        shown = 0
        for f in results:
            cases = f.get("assertionResults") or []
            if not cases:
                continue
            block = [Paragraph(_short(f.get("name", "")), st["cellb"])]
            rows = [[_P("Case", st, "cellb"), _P("Status", st, "cellb"),
                     _P("ms", st, "cellb")]]
            for c in cases:
                if shown >= MAX_CASES:
                    break
                title = " › ".join([*(c.get("ancestorTitles") or []),
                                    c.get("title", "")]).strip(" ›")
                ms = c.get("duration")
                rows.append([_P(title, st),
                             _P(c.get("status", ""), st),
                             _P(f"{ms:.0f}" if isinstance(ms, (int, float)) else "—", st)])
                shown += 1
            block.append(_table(rows, [W * 0.68, W * 0.18, W * 0.14]))
            block.append(Spacer(1, 8))
            story.append(KeepTogether(block))
            if shown >= MAX_CASES:
                story.append(Paragraph(
                    f"Stopped after {MAX_CASES} cases — the rest are in "
                    f"vitest.json.", st["small"]))
                break

        failing = [(f, c) for f in results for c in (f.get("assertionResults") or [])
                   if c.get("status") == "failed"]
        if failing:
            story.append(PageBreak())
            h2("Failures in full")
            for f, c in failing[:40]:
                story.append(Paragraph(
                    f"<b>{_short(f.get('name', ''))}</b> — {c.get('title', '')}",
                    st["body"]))
                for msg in (c.get("failureMessages") or [])[:2]:
                    story.append(_code(str(msg)[:1800], st))
                story.append(Spacer(1, 6))

    story.append(PageBreak())
    h1("3. Test timeline")
    history = qa.get("history") or []
    if not history:
        _missing(story, st, "the timeline")
    else:
        story.append(Paragraph(
            "Each round is one pass of writing and repairing tests. The rate "
            "is what the suite scored at the end of that round, against the "
            "floor it had to clear.", st["body"]))
        story.append(Spacer(1, 6))
        rows = [[_P("When", st, "cellb"), _P("Cases", st, "cellb"),
                 _P("Passed", st, "cellb"), _P("Failed", st, "cellb"),
                 _P("Rate", st, "cellb"), _P("Floor", st, "cellb"),
                 _P("Main causes", st, "cellb")]]
        for h in history:
            top = ", ".join(f"{t.get('class')} ×{t.get('count')}"
                            for t in (h.get("top") or [])[:3])
            rows.append([_P(str(h.get("at", ""))[:19].replace("T", " "), st),
                         _P(h.get("cases", "—"), st), _P(h.get("passed", "—"), st),
                         _P(h.get("failed", "—"), st),
                         _P(f"{h.get('rate', '—')}%", st),
                         _P(f"{h.get('floor', '—')}%", st), _P(top or "—", st)])
        story.append(_table(rows, [W * 0.17, W * 0.09, W * 0.09, W * 0.09,
                                   W * 0.09, W * 0.09, W * 0.38]))

    story.append(PageBreak())
    h1("4. Integration")
    if not e2e.get("ran"):
        _missing(story, st, "the end-to-end flow")
    else:
        story.append(_table(
            [[_P("Flow", st, "cellb"), _P(e2e.get("flow", "—"), st)],
             [_P("Passed", st, "cellb"), _P(e2e.get("passed", 0), st)],
             [_P("Failed", st, "cellb"), _P(e2e.get("failed", 0), st)],
             [_P("Repaired", st, "cellb"), _P(e2e.get("fixed", 0), st)]],
            [W * 0.28, W * 0.72], header=False))

    story.append(PageBreak())
    h1("5. API contracts")
    manifest = qa.get("manifest") or {}
    if not manifest:
        _missing(story, st, "the test manifest")
    else:
        story.append(Paragraph(
            "Which test covers which file, and how far into the build it was "
            "written. A stale entry is one whose target changed after the test "
            "was written.", st["body"]))
        story.append(Spacer(1, 6))
        rows = [[_P("Test", st, "cellb"), _P("Covers", st, "cellb"),
                 _P("Phase", st, "cellb"), _P("Tier", st, "cellb"),
                 _P("Stale", st, "cellb")]]
        for test, meta in sorted(manifest.items()):
            meta = meta if isinstance(meta, dict) else {}
            rows.append([_P(_short(test), st), _P(meta.get("target", "—"), st),
                         _P(meta.get("phase", "—"), st), _P(meta.get("tier", "—"), st),
                         _P("yes" if meta.get("stale") else "no", st)])
        story.append(_table(rows, [W * 0.34, W * 0.34, W * 0.10, W * 0.10, W * 0.12]))

    story.append(PageBreak())
    h1("6. Bug reports")
    runtime = report.get("runtime") or []
    failures = suite.get("failures") or []
    if not runtime and not failures:
        story.append(Paragraph(
            "No runtime issues and no unresolved test failures were recorded.",
            st["body"]))
    if failures:
        h2("Test failures")
        rows = [[_P("Test", st, "cellb"), _P("Why", st, "cellb")]]
        for f in failures[:60]:
            if isinstance(f, dict):
                rows.append([_P(f.get("test") or f.get("file") or "—", st),
                             _P(f.get("why") or f.get("message") or "—", st)])
            else:
                rows.append([_P(str(f), st), _P("—", st)])
        story.append(_table(rows, [W * 0.38, W * 0.62]))
        story.append(Spacer(1, 8))
    if runtime:
        h2("Runtime issues")
        rows = [[_P("Where", st, "cellb"), _P("What", st, "cellb")]]
        for r in runtime[:60]:
            if isinstance(r, dict):
                rows.append([_P(r.get("route") or r.get("where") or "—", st),
                             _P(r.get("message") or r.get("error") or str(r)[:160], st)])
            else:
                rows.append([_P("—", st), _P(str(r)[:160], st)])
        story.append(_table(rows, [W * 0.32, W * 0.68]))

    story.append(PageBreak())
    h1("7. Security")
    if not sec.get("ran"):
        _missing(story, st, "the security scan")
    else:
        findings = sec.get("findings") or []
        if not findings:
            story.append(Paragraph("The scan ran and found nothing.", st["body"]))
        else:
            rows = [[_P("Severity", st, "cellb"), _P("Where", st, "cellb"),
                     _P("Finding", st, "cellb")]]
            for f in findings[:80]:
                f = f if isinstance(f, dict) else {"message": str(f)}
                rows.append([_P(f.get("severity", "—"), st),
                             _P(f.get("path") or f.get("where") or "—", st),
                             _P(f.get("message") or f.get("title") or "—", st)])
            story.append(_table(rows, [W * 0.16, W * 0.30, W * 0.54]))
        audit = sec.get("audit") or {}
        if audit:
            story.append(Spacer(1, 8))
            h2("Dependency audit")
            story.append(_code(str(audit)[:2000], st))

    story.append(PageBreak())
    h1("8. Performance")
    perf = qa.get("performance") or {}
    scores = perf.get("scores") or {}
    inline = {k: v for k, v in (report.get("performance") or {}).items()
              if isinstance(v, (int, float))}
    scores = scores or inline
    if not scores:
        _missing(story, st, "performance")
    else:
        rows = [[_P("Category", st, "cellb"), _P("Score", st, "cellb")]]
        for k, v in scores.items():
            cell = Paragraph(f'<font color="#{_score_colour(v).hexval()[2:]}">'
                             f"<b>{v}</b></font>", st["cell"])
            rows.append([_P(k.replace("-", " ").title(), st), cell])
        story.append(_table(rows, [W * 0.55, W * 0.45]))
        metrics = perf.get("metrics") or {}
        if metrics:
            story.append(Spacer(1, 8))
            h2("Metrics")
            story.append(_table(
                [[_P("Metric", st, "cellb"), _P("Value", st, "cellb")]]
                + [[_P(k.replace("-", " ").title(), st), _P(v, st)]
                   for k, v in metrics.items()],
                [W * 0.55, W * 0.45]))
        note = (report.get("performance") or {}).get("measured_on") or perf.get("fetchTime")
        if note:
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"Measured on: {note}. A development server is slower than a "
                f"production build, so the performance score is a floor rather "
                f"than a verdict.", st["small"]))

    tests = qa.get("tests") or {}
    if tests:
        story.append(PageBreak())
        h1("Appendix A. The tests themselves")
        story.append(Paragraph(
            "The evidence behind every number above. Long files are cut, and "
            "the cut is marked.", st["body"]))
        for name in sorted(tests):
            body = tests[name] or ""
            block = [Spacer(1, 8), Paragraph(name, st["h2"])]
            cut = len(body) > SOURCE_CHARS
            block.append(_code(body[:SOURCE_CHARS], st))
            if cut:
                block.append(Paragraph(
                    f"… cut here — {len(body) - SOURCE_CHARS} more characters "
                    f"in the file on disk.", st["small"]))
            story.append(KeepTogether(block) if len(body) < 2500 else block[0])
            if len(body) >= 2500:
                for f in block[1:]:
                    story.append(f)

    doc.multiBuild(story)
    return out


def _short(path: str) -> str:
    """The part of a test path worth reading in a table cell."""
    p = str(path or "").replace("\\", "/")
    i = p.find("/tests/")
    return p[i + 1:] if i >= 0 else (p.rsplit("/", 3)[-1] if "/" in p else p)


__all__ = ["build_qa_pdf"]
