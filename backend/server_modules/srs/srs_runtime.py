# SRS adoption and project handoff helpers.
def _srs_get(path: str, timeout: float = 20.0):
    """One read from the SRS agent."""
    try:
        r = requests.get(f"http://127.0.0.1:{SRS_PORT}{path}", timeout=timeout)
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


def adopt_srs(srs_id: str, proj_dir: Path) -> bool:
    """Move an SRS into the project it produced."""
    from datetime import datetime, timezone
    try:
        staging = PROD_DIR / ".srs" / srs_id
        dest = proj_dir / ".agentforge" / "srs"
        dest.mkdir(parents=True, exist_ok=True)

        copied = 0
        if staging.is_dir():
            for src in staging.rglob("*"):
                if not src.is_file():
                    continue
                target = dest / src.relative_to(staging)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                copied += 1

        base = f"/projects/{srs_id}"

        plan = _srs_get(f"{base}/plan")
        if plan is not None:
            body = plan.json()
            markdown = body.get("markdown") or ""
            if markdown:
                (dest / "plan.md").write_text(markdown, encoding="utf-8")
            (dest / "plan.json.srs").write_text(
                json.dumps(body.get("plan") or {}, indent=2), encoding="utf-8")

        handoff = _srs_get(f"{base}/builder-handoff")
        if handoff is not None:
            body = handoff.json()
            (dest / "handoff.json").write_text(json.dumps(body, indent=2),
                                               encoding="utf-8")
            (dest / "handoff.txt").write_text(body.get("prompt") or "",
                                              encoding="utf-8")

        interview = _srs_get(f"{base}/interview")
        if interview is not None:
            (dest / "interview.json").write_text(
                json.dumps(interview.json(), indent=2), encoding="utf-8")

        if not (dest / "srs_latest.json").exists():
            document = _srs_get(f"{base}/srs-json")
            if document is not None:
                (dest / "srs_latest.json").write_text(
                    json.dumps(document.json(), indent=2), encoding="utf-8")
                elog("INFO", "   📄 SRS document fetched (staging had none)")

        d_dir = dest / "diagrams"
        have_diagrams = d_dir.is_dir() and any(d_dir.glob("*.mmd"))
        if not have_diagrams:
            drawn = _srs_get(f"{base}/diagrams")
            if drawn is not None:
                d_dir.mkdir(parents=True, exist_ok=True)
                wrote = 0
                for item in (drawn.json() or {}).get("diagrams") or []:
                    kind = str(item.get("kind") or item.get("name") or "").strip()
                    source = item.get("source") or item.get("mermaid") or ""
                    if not kind or not source:
                        continue
                    (d_dir / f"{kind}.mmd").write_text(source, encoding="utf-8")
                    wrote += 1

                    if item.get("svg"):
                        (d_dir / f"{kind}.svg").write_text(item["svg"],
                                                           encoding="utf-8")
                if wrote:
                    elog("INFO", f"   🖼 {wrote} diagram(s) fetched (staging had none)")

        pdf = _srs_get(f"{base}/download/pdf", timeout=120)
        if pdf is not None and pdf.content:
            (dest / "SRS_latest.pdf").write_bytes(pdf.content)

        (dest / "link.json").write_text(json.dumps({
            "srs_id": srs_id,
            "adopted_at": datetime.now(timezone.utc).isoformat(),
            "staged_at": str(staging),
            "files_copied": copied,
        }, indent=2), encoding="utf-8")

        elog("INFO", f"   📄 SRS adopted from {srs_id} ({copied} files)")
        return True
    except Exception as e:
        elog("WARN", f"   ⚠️  Could not adopt the SRS: {type(e).__name__}: {e}")
        return False


def _srs_app_name(srs_id: str) -> str:
    """What the customer called their app, from the staged SRS."""
    if not srs_id:
        return ""
    try:
        doc = json.loads((PROD_DIR / ".srs" / srs_id / "srs_latest.json")
                         .read_text(encoding="utf-8"))
        doc = doc.get("srs_document") or doc
        summary = doc.get("app_summary")
        for candidate in (((summary or {}).get("app_name")
                           if isinstance(summary, dict) else ""),
                          doc.get("project_name")):
            name = " ".join(str(candidate or "").split())
            if _is_a_name(name, limit=40):
                return name
        return ""
    except Exception:
        return ""


def _srs_name_line(proj_dir: Path) -> str:
    """What the customer named their app, said before anything else."""
    try:
        doc = json.loads((proj_dir / ".agentforge" / "srs" / "srs_latest.json")
                         .read_text(encoding="utf-8"))
        doc = doc.get("srs_document") or doc
    except Exception:
        return ""
    summary = doc.get("app_summary")
    name = ((summary or {}).get("app_name") if isinstance(summary, dict)
            else "") or doc.get("project_name") or ""
    name = " ".join(str(name).split())
    if not _is_a_name(name):
        return ""
    return (f"THE APP IS CALLED “{name}”. The customer chose that name. Use it "
            f"exactly, in the header, the page title, the sign-in screen and "
            f"`package.json`. Do not invent another one.\n\n")


def _srs_brief(proj_dir: Path, model: str) -> str:
    """What the SRS knows that its handoff prompt had to leave out."""
    srs_dir = proj_dir / ".agentforge" / "srs"
    try:
        doc = json.loads((srs_dir / "srs_latest.json").read_text(encoding="utf-8"))
        doc = doc.get("srs_document") or doc
    except Exception:
        return ""
    if not doc:
        return ""

    def rows(items, key=None, id_key=None):
        """One line per item, whatever shape the item is."""
        if isinstance(items, str):
            items = [items]

        out = []
        for item in (items or []):
            ident = ""
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = (item.get(key) if key else None) or (
                    item.get("requirement") or item.get("description")
                    or item.get("criterion") or item.get("rule")
                    or item.get("text") or item.get("page_name")
                    or item.get("role_name") or item.get("name") or "")
                if id_key:
                    ident = " ".join(str(item.get(id_key) or "").split())
            else:
                text = str(item)
            text = " ".join(str(text).split())
            if text:
                out.append(f"- {ident}  {text}" if ident else f"- {text}")
        return out

    def page_rows(pages):
        """Name, route, who may open it, and what it is for."""
        out = []
        for p in (pages or []):
            if isinstance(p, str):
                out.append(f"- {p}")
                continue
            if not isinstance(p, dict):
                continue
            name = " ".join(str(p.get("page_name") or p.get("name") or "").split())
            if not name:
                continue
            line = f"- {name}"
            if p.get("route"):
                line += f"  ({p['route']})"
            roles = [str(r) for r in (p.get("allowed_roles") or []) if str(r).strip()]
            if roles:
                line += f"  — {', '.join(roles)}"
            fns = [" ".join(str(f).split()) for f in (p.get("functions") or [])
                   if str(f).strip()]
            if fns:
                line += f"\n    {' '.join(fns)}"
            out.append(line)
        return out

    parts = ["\n\n---\n\nThe approved specification this was planned from.",
             "The prompt above is a short summary of it. Where the two differ, "
             "follow this specification — except where the prompt states a fact "
             "about this machine, such as the app's name or a logo already on "
             "disk, which it knows and this does not."]

    titles = []

    def section(title, lines):
        if lines:
            parts.append(f"\n{title}\n" + "\n".join(lines))
            titles.append(title)

    def column(c):
        """One column with everything the schema actually decided about it."""
        if isinstance(c, str):
            return c
        if not isinstance(c, dict):
            return ""
        name = c.get("name") or c.get("column") or ""
        if not name:
            return ""
        flags = []
        if c.get("type"):
            flags.append(str(c["type"]))
        if c.get("primary_key") or c.get("primary"):
            flags.append("primary key")
        if c.get("unique"):
            flags.append("unique")
        ref = c.get("references") or c.get("foreign_key")
        if ref:
            flags.append(f"references {ref}")
        vals = c.get("values") or c.get("enum")
        if vals:
            shown = ", ".join(str(v) for v in list(vals)[:8])
            flags.append(f"one of: {shown}")
        if c.get("default") is not None and c.get("default") != "":
            flags.append(f"default {c['default']}")
        if c.get("nullable") is True or c.get("required") is False:
            flags.append("optional")
        return f"{name} ({'; '.join(flags)})" if flags else str(name)

    database = doc.get("database_design") or {}
    tables = database.get("tables") or []
    shaped = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        name = t.get("table_name") or t.get("name")
        if not name:
            continue
        cols = [column(c) for c in (t.get("columns") or t.get("fields") or [])]
        cols = [c for c in cols if c]
        head = f"- {name}"
        why = " ".join(str(t.get("description") or "").split())
        if why:
            head += f" — {why}"
        shaped.append(head + "\n    " + ("\n    ".join(cols) or "no columns given"))

    for rel in (database.get("relationships") or []):
        if not isinstance(rel, dict):
            continue
        frm, to = rel.get("from"), rel.get("to")
        if not (frm and to):
            continue
        kind = str(rel.get("type") or "").replace("_", " ")
        shaped.append(f"- {frm} → {to}" + (f"  ({kind})" if kind else ""))

    def requirement_rows(items):
        """Requirements with their id, module and priority."""
        out = []
        for r in (items or []):
            if isinstance(r, str):
                out.append(f"- {r}")
                continue
            if not isinstance(r, dict):
                continue
            text = " ".join(str(r.get("requirement") or r.get("description")
                                or r.get("text") or "").split())
            if not text:
                continue
            ident = " ".join(str(r.get("id") or "").split())
            tail = [str(r[k]) for k in ("module", "priority") if r.get(k)]
            line = f"- {ident}  {text}" if ident else f"- {text}"
            if tail:
                line += f"   [{' · '.join(tail)}]"
            out.append(line)
        return out

    def role_rows(items):
        """Each role and the duties its description spells out."""
        out = []
        for r in (items or []):
            if isinstance(r, str):
                out.append(f"- {r}")
                continue
            if not isinstance(r, dict):
                continue
            name = " ".join(str(r.get("role_name") or r.get("name") or "").split())
            if not name:
                continue
            why = " ".join(str(r.get("description") or "").split())
            out.append(f"- {name}" + (f" — {why}" if why else ""))
        return out

    def route_rows(items):
        """The API the specification already decided on."""
        out = []
        for e in (items or []):
            if isinstance(e, str):
                out.append(f"- {e}")
                continue
            if not isinstance(e, dict):
                continue
            path = e.get("path") or e.get("endpoint")
            if not path:
                continue
            method = str(e.get("method") or "GET").upper()
            line = f"- {method} {path}"
            why = " ".join(str(e.get("description") or "").split())
            if why:
                line += f" — {why}"
            if e.get("auth_required") is True:
                line += "   [signed in]"
            elif e.get("auth_required") is False:
                line += "   [public]"
            out.append(line)
        return out

    def trace_rows(items):
        """Which requirement is answered by which page and which table."""
        out = []
        for row in (items or []):
            if not isinstance(row, dict):
                continue
            rid = row.get("requirement_id") or row.get("id")
            if not rid:
                continue
            pages = ", ".join(str(p) for p in (row.get("pages") or []))
            tables_ = ", ".join(str(t) for t in (row.get("tables") or []))
            bits = []
            if pages:
                bits.append(f"pages: {pages}")
            if tables_:
                bits.append(f"data: {tables_}")
            if not bits:
                continue
            out.append(f"- {rid} → " + "; ".join(bits))
        return out

    def look_rows():
        """The colour and the components the customer actually asked for."""
        out = []
        brand = doc.get("branding") or {}
        if isinstance(brand, dict):
            bits = [f"{k.replace('_', ' ')}: {v}"
                    for k, v in brand.items() if isinstance(v, (str, int)) and v]
            if bits:
                out.append("- " + "; ".join(bits))
        ux = doc.get("ui_ux_requirements") or {}
        if isinstance(ux, dict):
            need = ux.get("required_components") or []
            if need:
                out.append("- every screen is built from: "
                           + ", ".join(str(c) for c in need))
            rest = [f"{k.replace('_', ' ')}: {v}" for k, v in ux.items()
                    if k != "required_components" and isinstance(v, (str, bool, int))]
            if rest:
                out.append("- " + "; ".join(rest))
        return out

    def flag_rows(obj, label=""):
        """A small settings object, one line, skipping what it left empty."""
        if not isinstance(obj, dict):
            return []
        bits = [f"{k.replace('_', ' ')}: {v}" for k, v in obj.items()
                if isinstance(v, (str, bool, int)) and v not in ("", None)]
        return [f"- {label}" + "; ".join(bits)] if bits else []

    matrix = []
    for row in (doc.get("role_access_matrix") or []):
        if not isinstance(row, dict):
            continue
        who = row.get("role") or row.get("role_name")
        if not who:
            continue
        pages = ", ".join(str(p) for p in (row.get("allowed_pages") or []))
        can = ", ".join(str(f) for f in (row.get("allowed_functions") or []))
        matrix.append(f"- {who} may open: {pages or 'nothing listed'}"
                      + (f"\n    and may: {can}" if can else ""))

    flows = []
    for w in (doc.get("business_workflows") or []):
        if not isinstance(w, dict):
            continue
        name = w.get("workflow_name") or w.get("name")
        steps = [" ".join(str(s).split()) for s in (w.get("steps") or [])
                 if str(s).strip()]
        if name and steps:
            flows.append(f"- {name}: "
                         + " → ".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))

    section("REQUIREMENTS", requirement_rows(doc.get("functional_requirements")))
    section("DATA", shaped)
    section("THE ROUTES IT MUST SERVE", route_rows(doc.get("api_design")))
    section("ROLES", role_rows(doc.get("roles")))
    section("PAGES THAT REQUIRE A LOGIN", page_rows(doc.get("protected_pages")))
    section("PAGES THAT MUST BE PUBLIC", page_rows(doc.get("public_pages")))
    section("WHO CAN REACH WHAT", matrix)
    section("WHICH REQUIREMENT LIVES WHERE",
            trace_rows(doc.get("requirement_traceability_matrix")))
    section("HOW THE WORK FLOWS", flows)
    section("DONE MEANS", rows(doc.get("acceptance_criteria"), key="criterion",
                               id_key="id"))
    section("VALIDATION RULES", rows(doc.get("validation_rules"), key="rule",
                                     id_key="field"))
    section("SIGNING IN", flag_rows(doc.get("authentication_requirement")))
    section("SECURITY", rows(doc.get("security_requirements")))
    section("HOW IT SHOULD LOOK", look_rows())
    section("THE MODULES IT IS MADE OF", rows(doc.get("main_modules")))
    def report_rows(items):
        """A report, its filters and the formats it exports."""
        out = []
        for r in (items or []):
            if isinstance(r, str):
                out.append(f"- {r}")
                continue
            if not isinstance(r, dict):
                continue
            name = " ".join(str(r.get("report_name") or r.get("name") or "").split())
            if not name:
                continue
            bits = []
            if r.get("filters"):
                bits.append("filtered by " + ", ".join(str(f) for f in r["filters"]))
            if r.get("exports"):
                bits.append("exports " + ", ".join(str(e) for e in r["exports"]))
            out.append(f"- {name}" + (f" — {'; '.join(bits)}" if bits else ""))
        return out

    section("REPORTS IT MUST PRODUCE", report_rows(doc.get("reporting_requirements")))
    section("QUALITIES", rows(doc.get("non_functional_requirements"), id_key="id"))
    section("LIMITS AGREED", rows(doc.get("constraints")))
    section("ASSUMPTIONS ALREADY AGREED", rows(doc.get("assumptions")))
    section("WHAT THE CUSTOMER ADDED", rows(doc.get("customer_notes")))

    text = "\n".join(parts)

    from agents.architect import CHARS_PER_TOKEN, HISTORY_BUDGET
    budget = int(max_context(model) * HISTORY_BUDGET * CHARS_PER_TOKEN / 6)
    if len(text) > budget:
        cut = text.rfind("\n", 0, budget)
        dropped = text[cut:].count("\n- ") if cut > 0 else text.count("\n- ")

        def bullets(body: str, title: str) -> int:
            """Rows belonging to `title` alone, not to everything after it."""
            if f"\n{title}\n" not in body:
                return 0
            rest = body.split(f"\n{title}\n", 1)[1]
            end = rest.find("\n\n")  # The next section's blank line
            return (rest[:end] if end > 0 else rest).count("\n- ") + \
                   (1 if (rest[:end] if end > 0 else rest).startswith("- ") else 0)

        full = {t: bullets(text, t) for t in titles}
        text = text[:cut if cut > 0 else budget] + "\n"

        lost, partial = [], []
        for t in titles:
            if f"\n{t}\n" not in text:
                lost.append(t)
                continue
            kept = bullets(text, t)
            if kept < full.get(t, kept):
                partial.append(f"{t} ({kept} of {full[t]})")
        elog("WARN", f"   ✂ SRS brief trimmed to {budget:,} chars for {model} — "
                     f"{dropped} line(s) left out (they are all in the SRS tab)"
                     + (f"; sections lost: {', '.join(lost)}" if lost else "")
                     + (f"; cut short: {', '.join(partial)}" if partial else ""))
    elog("INFO", f"   📄 Architect briefed with the SRS ({len(text):,} chars "
                 f"of a {budget:,} budget)")
    return text


def read_srs_results(proj_name: str) -> dict:
    """Everything the SRS tab shows, gathered server-side."""
    proj_dir = PROD_DIR / proj_name
    if not proj_dir.is_dir():
        return {"error": f"no such project: {proj_name}"}
    srs_dir = proj_dir / ".agentforge" / "srs"

    def load(path, default=None):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def text(path, default=""):
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return default

    out = {"project": proj_name, "have": {}}
    if not srs_dir.is_dir():

        out["have"] = {k: False for k in
                       ("document", "plan", "handoff", "interview",
                        "diagrams", "pdf")}
        return out

    out["link"] = load(srs_dir / "link.json", {}) or {}

    document = load(srs_dir / "srs_latest.json", {}) or {}
    out["document"] = document.get("srs_document") or document
    out["have"]["document"] = bool(out["document"])

    out["plan"] = text(srs_dir / "plan.md")
    out["have"]["plan"] = bool(out["plan"].strip())

    out["handoff"] = load(srs_dir / "handoff.json", {}) or {}
    out["have"]["handoff"] = bool(out["handoff"].get("prompt"))

    out["interview"] = load(srs_dir / "interview.json", {}) or {}
    out["have"]["interview"] = bool(out["interview"].get("transcript"))

    diagrams = []
    d_dir = srs_dir / "diagrams"
    if d_dir.is_dir():
        for mmd in sorted(d_dir.glob("*.mmd")):
            svg = mmd.with_suffix(".svg")
            body = text(svg) if svg.is_file() else ""
            diagrams.append({
                "name": mmd.stem,
                "mermaid": text(mmd),
                "svg": body if 0 < len(body) <= 400_000 else "",
                "png": (mmd.with_suffix(".png")).is_file(),
            })
    out["diagrams"] = diagrams
    out["have"]["diagrams"] = bool(diagrams)

    out["have"]["pdf"] = (srs_dir / "SRS_latest.pdf").is_file()
    return out
