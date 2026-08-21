"""Render the compact AgentForge build contract."""
from __future__ import annotations

from .builder_access import _flow_section
from .builder_auth import auth_prompt_lines
from .builder_features import feature_prompt_lines
from .builder_constants import MAX_CHARS, MAX_WORDS
from .builder_utils import _clean, _page_file

def render_prompt(handoff: dict, plan: dict, *, auth: bool = False,
                  srs_document: dict | None = None) -> str:
    """Render the AgentForge-facing build contract."""
    plan = plan or {}
    doc = srs_document or {}
    name = _clean(handoff.get("app_name") or plan.get("app_name") or "")
    kind = _clean(handoff.get("appType") or "Web application")
    intent = _clean(handoff.get("product_intent") or plan.get("product_intent") or "")
    source = [r for r in (handoff.get("requirements") or []) if r.get("source_id")]
    supplementary = [r for r in (handoff.get("requirements") or [])
                     if not r.get("source_id") and r.get("kind") != "page"]

    lines: list[str] = [
        "AGENTFORGE BUILD HANDOFF v4 — AUTHORITATIVE PRODUCT CONTRACT",
        "",
        (f"APP NAME: {name}" if name else f"APP TYPE: {kind}"),
        (f"APP TYPE: {kind}" if name else ""),
        (f"PRODUCT INTENT: {intent}" if intent else ""),
        "",
        "SOURCE OF TRUTH:",
        "- This handoff comes from the customer-approved SRS and approved plan. Do not silently shrink, reinterpret or replace it with a sample CRUD app.",
        "- AgentForge owns the implementation plan and stack. Preserve every product capability even when the SRS mentions an integration or technology the runtime cannot use exactly as named; surface the incompatibility instead of deleting the requirement.",
        "- Keep requirement ids. Every FR-* below must survive into the Architect plan as a machine-readable capability and into at least one task `covers` entry.",
        "",
        "NON-NEGOTIABLE DEFINITION OF DONE:",
        "- Build the COMPLETE application. No 'coming soon', TODO page, decorative form, permanently disabled feature, console-only button or dead link counts as implementation.",
        "- Every user-visible action has observable proof. Browser-visible capabilities are `e2e=true` and are covered by a walkable journey. If the SRS has no journey for one, generate a minimal meaningful E2E journey instead of skipping E2E.",
        "- Every route named below is served by a real App Router page/handler and every navigation target exists. Dynamic detail links are tested with real seeded record ids, not only with the `[id]` file existing.",
        "- Every mutation persists to MongoDB and the next visible state proves it happened. A successful HTTP response with unchanged UI/data is not complete.",
        "- Access control is enforced server-side before protected data is read or written. Hiding a nav item is not authorization.",
        "- Mongo relation/ObjectId fields are not plain browser strings: validate URL/form/session ids and convert to ObjectId before Mongo queries; serialize ObjectIds back to strings at client boundaries.",
        "- Do not change an approved data type, route, role boundary or workflow merely to make a generated test pass. Repair the test when its assumption is wrong; repair the app when behavior/contract evidence is wrong.",
        "",
    ]
    if source:
        lines.append(f"AUTHORITATIVE FUNCTIONAL REQUIREMENTS ({len(source)}):")
        for r in source:
            meta = [x for x in (r.get("module"), r.get("priority")) if x]
            proof = r.get("proof") or {}
            trace_bits = []
            if proof.get("pages"):
                trace_bits.append("pages=" + ",".join(proof["pages"]))
            if proof.get("tables"):
                trace_bits.append("data=" + ",".join(proof["tables"]))
            if proof.get("test_case"):
                trace_bits.append("test=" + str(proof["test_case"]))
            tail = (f" [{' · '.join(meta)}]" if meta else "")
            if trace_bits:
                tail += "  -> " + "; ".join(trace_bits)
            lines.append(f"- {r['source_id']}: {r['text']}.{tail}")
        lines.append("")
    else:
        lines += ["AUTHORITATIVE FUNCTIONAL REQUIREMENTS:",
                  "- No FR ids were present in the SRS. Treat every meaningful user verb in the approved plan as a source requirement; do not merge away distinct actions.", ""]

    pages = handoff.get("pages") or []
    if pages:
        lines.append(f"PAGES AND EXACT ROUTES ({len(pages)}):")
        for p in pages:
            access = "public" if p.get("public") else (
                "roles=" + ",".join(p.get("allowed_roles") or [])
                if p.get("allowed_roles") else "signed-in")
            job = _clean(p.get("purpose") or "")
            line = f"- {p.get('name')}  {p.get('route')}  [{access}]  file={p.get('expected_file') or _page_file(p.get('route'))}"
            if job:
                line += f" — {job}"
            lines.append(line)
        lines += ["", "ROUTE RULE: a route in this list without its page file is a 404; a page with no incoming journey/nav link is unreachable. Both are blockers.", ""]

    lines += _flow_section(plan, pages, doc)
    lines += auth_prompt_lines(handoff.get("auth_contract") or {"enabled": bool(auth)})
    lines += feature_prompt_lines(handoff.get("feature_contracts") or [])

    models = handoff.get("models") or []
    if models:
        lines.append("DATA MODEL — TYPES AND REFERENCES ARE AUTHORITATIVE:")
        for m in models:
            lines.append(f"- {m.get('name')}  collection/table={m.get('table') or m.get('name')}")
            for f in (m.get("field_specs") or []):
                flags = [str(f.get("type") or "String")]
                if f.get("references"):
                    flags.append("references " + str(f["references"]))
                if f.get("primary_key"):
                    flags.append("primary key")
                if f.get("unique"):
                    flags.append("unique")
                if f.get("required") is True:
                    flags.append("required")
                elif f.get("required") is False:
                    flags.append("optional")
                if f.get("enum"):
                    flags.append("one of " + ", ".join(str(x) for x in f["enum"]))
                if f.get("default") not in (None, ""):
                    flags.append("default=" + str(f["default"]))
                lines.append(f"    {f.get('name')}: " + "; ".join(flags))
        for rel in (handoff.get("relationships") or []):
            if rel.get("from") and rel.get("to"):
                lines.append(f"- relationship: {rel['from']} -> {rel['to']}"
                             + (f" ({rel.get('type')})" if rel.get("type") else "")
                             + (f" via {rel.get('via')}" if rel.get("via") else ""))
        lines += ["", "DATA BOUNDARY RULE: any field typed ObjectId or referencing another collection is queried as ObjectId on the server. `findOne({_id: id})` with a URL string is not equivalent to `findOne({_id: new ObjectId(id)})`.", ""]

    apis = handoff.get("apis") or []
    if apis:
        lines.append("API CONTRACT — DO NOT INVENT DIFFERENT PATHS:")
        for ep in apis:
            access = ""
            if ep.get("allowed_roles"):
                access = " roles=" + ",".join(ep["allowed_roles"])
            elif ep.get("auth_required") is True:
                access = " signed-in"
            elif ep.get("auth_required") is False:
                access = " public"
            lines.append(f"- {ep.get('method')} {ep.get('path')} [{ep.get('expected_file')}]" + access
                         + (f" — {ep.get('description')}" if ep.get("description") else ""))
        lines.append("")

    trace = handoff.get("traceability") or {}
    if trace:
        lines.append("TRACEABILITY — USE THIS TO PLACE CAPABILITIES ON FILES/TASKS:")
        for rid, row in trace.items():
            bits = []
            if row.get("pages"):
                bits.append("pages=" + ", ".join(row["pages"]))
            if row.get("tables"):
                bits.append("data=" + ", ".join(row["tables"]))
            if row.get("test_case"):
                bits.append("test=" + str(row["test_case"]))
            lines.append(f"- {rid}: " + ("; ".join(bits) or "no target recorded — planner must assign one"))
        lines.append("")

    validations = handoff.get("validation_rules") or []
    if validations:
        lines.append("VALIDATION RULES:")
        for v in validations:
            lines.append(f"- {v.get('field') or 'input'}: {v.get('rule')}")
        lines.append("")

    acceptance = handoff.get("acceptance_criteria") or []
    if acceptance:
        lines.append("ACCEPTANCE PROOF:")
        for c in acceptance:
            prefix = (str(c.get("id")) + ": ") if c.get("id") else ""
            lines.append(f"- {prefix}{c.get('criterion')}")
        lines.append("")

    if supplementary:
        lines.append("SUPPLEMENTARY APPROVED PLAN ACTIONS (do not let these disappear when mapping FR ids):")
        for r in supplementary:
            lines.append(f"- {r.get('text')}")
        lines.append("")

    integrations = handoff.get("integration_requirements") or []
    if integrations:
        lines.append("INTEGRATIONS — PRODUCT BEHAVIOR MUST NOT BE SILENTLY DROPPED:")
        for item in integrations:
            name = _clean(item.get("name") or "Integration")
            desc = _clean(item.get("description") or "")
            req = " required" if item.get("required") is True else ""
            lines.append(f"- {name}{req}" + (f" — {desc}" if desc else ""))
        lines.append("")

    notifications = handoff.get("notification_rules") or []
    if notifications:
        lines.append("NOTIFICATIONS:")
        for item in notifications:
            event = _clean(item.get("event") or "event")
            rec = ", ".join(str(x) for x in (item.get("recipients") or []))
            channels = ", ".join(str(x) for x in (item.get("channels") or []))
            lines.append(f"- {event}: recipients={rec or 'as specified'}; channels={channels or 'as specified'}")
        lines.append("")

    reports = handoff.get("reporting_requirements") or []
    if reports:
        lines.append("REPORTS:")
        for item in reports:
            name = _clean(item.get("report_name") or item.get("name") or "Report")
            filters = ", ".join(str(x) for x in (item.get("filters") or []))
            exports = ", ".join(str(x) for x in (item.get("exports") or []))
            tail = []
            if filters: tail.append("filters=" + filters)
            if exports: tail.append("exports=" + exports)
            lines.append(f"- {name}" + (" — " + "; ".join(tail) if tail else ""))
        lines.append("")

    nfrs = handoff.get("non_functional_requirements") or []
    if nfrs:
        lines.append("NON-FUNCTIONAL REQUIREMENTS:")
        for item in nfrs:
            if isinstance(item, dict):
                rid = _clean(item.get("id") or "")
                req = _clean(item.get("requirement") or item.get("description") or "")
                cat = _clean(item.get("category") or "")
                if req:
                    lines.append(f"- {rid + ': ' if rid else ''}{req}" + (f" [{cat}]" if cat else ""))
            elif _clean(item):
                lines.append("- " + _clean(item))
        lines.append("")

    security = [str(x).strip() for x in (doc.get("security_requirements") or []) if str(x).strip()]
    if security:
        lines.append("SECURITY REQUIREMENTS:")
        lines.extend("- " + x for x in security)
        lines.append("")

    look = _clean(plan.get("look_and_feel") or "")
    ux = doc.get("ui_ux_requirements") or {}
    if look or ux:
        lines.append("DESIGN CONTRACT:")
        if look:
            lines.append(f"- {look}")
        if isinstance(ux, dict):
            comps = ux.get("required_components") or []
            if comps:
                lines.append("- Required UI pieces: " + ", ".join(str(x) for x in comps))
        lines += ["- Design requirements never authorize fake controls. A disabled control is valid only for a real business state that can become enabled; it is not a placeholder for unimplemented work.", ""]

    lines += [
        "FINAL BUILDER CHECK:",
        "Before generation starts, prove that every FR/source requirement maps to a capability, exact files and (when browser-visible) a workflow. Before calling the app clean, run build + unit + real browser E2E against those journeys. If a required capability has no proof, the app is incomplete even when `npm run build` is green.",
    ]

    compact: list[str] = []
    for line in lines:
        if line == "" and compact and compact[-1] == "":
            continue
        compact.append(line)
    return _cap("\n".join(compact).strip() + "\n")


def _cap(text: str) -> str:
    """Return the complete handoff unless an explicit transport cap is configured."""
    if not MAX_WORDS and not MAX_CHARS:
        return text.rstrip() + "\n"

    lines = text.split("\n")
    kept: list[str] = []
    words = chars = 0
    for line in lines:
        count = len(line.split())
        if MAX_WORDS and words + count > MAX_WORDS:
            break
        if MAX_CHARS and chars + len(line) + 1 > MAX_CHARS:
            break
        kept.append(line)
        words += count
        chars += len(line) + 1
    return "\n".join(kept).rstrip() + "\n"
