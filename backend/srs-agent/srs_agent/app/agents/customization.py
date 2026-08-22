"""CustomizationAgent — edit an existing SRS from a plain-English prompt."""
from __future__ import annotations

import copy
import json
import re

from ..knowledge import plan_srs
from ..llm import LLMRepairFailed, LLMUnavailable, get_llm
from ..schemas.srs import validate_srs
from ..generators.standards import apply_international_profile
from ..services.events import bus
from .customer_context import customer_context
from .language import LanguageConversionError, is_english, output_language_instruction
from .state import AgentState


_DERIVED = ("diagrams", "approved_plan", "approved_plan_markdown",
            "builder_handoff", "branding", "effective_plan")


_VIEW_BUDGET = 12000

_SYS = (
    "You edit an existing ISO/IEC/IEEE 29148:2018-aligned SRS JSON based on a user's plain-English "
    "request. Apply ONLY what is asked. Return ONLY the top-level sections you "
    "actually changed, each one complete — every section you omit is kept from "
    "the current document, so never repeat an unchanged section and never "
    "return a section as an empty list. The document you are shown may be "
    "truncated; edit only what you can see. Return ONLY JSON of the form "
    '{"srs_document": {<changed sections only>}, '
    '"diff_summary": ["short bullet", ...]}.'
)


_REMOVAL = re.compile(
    r"\b(remove|delete|drop|exclude|scrap|get rid of|take out|no longer|"
    r"don'?t need|do not need)\b", re.IGNORECASE)


def _blank(value) -> bool:
    """An omitted section and an empty one mean the same thing here: no edit."""
    return value is None or (isinstance(value, (list, dict, str)) and not value)


def _editable_view(srs: dict) -> dict:
    """The document minus the derived sections — what the model may edit."""
    doc = (srs or {}).get("srs_document") or {}
    return {k: v for k, v in doc.items() if k not in _DERIVED}


_IDENTITY_KEYS = ("id", "table_name", "page_name", "role_key", "workflow_name",
                  "requirement_id", "role", "name", "field", "area", "category")


def _identity(item):
    """A stable handle for one entry, or None when it cannot be recognised."""
    if isinstance(item, dict):
        for key in _IDENTITY_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return (key, value.strip().casefold())
        return None
    if isinstance(item, str):
        return ("_text", " ".join(item.split()).casefold())
    return None


def _merge_list(new: list, current: list, removing: bool) -> list:
    """The patch's entries folded onto the current ones, never fewer."""
    if removing or len(new) >= len(current) or not current:
        return new
    idents = [_identity(x) for x in new]
    if any(i is None for i in idents):

        return current
    out = list(current)
    where = {}
    for i, item in enumerate(out):
        ident = _identity(item)
        if ident is not None and ident not in where:
            where[ident] = i
    for item, ident in zip(new, idents):
        if ident in where:
            at = where[ident]
            if _is_a_different_thing(item, out[at]):
                # Treat numbers as slots, not names.
                out.append(_renumbered(item, out))
            else:
                out[where[ident]] = item
        else:
            out.append(item)
    return out


_ID_KEYS = ("id", "requirement_id")
_ID_SHAPE = re.compile(r"^([A-Za-z]+-?)(\d+)$")


def _words(item) -> set:
    """The content words of an entry, for telling an edit from a new thing."""
    if isinstance(item, dict):
        text = " ".join(str(v) for k, v in item.items()
                        if k not in _ID_KEYS and isinstance(v, str))
    else:
        text = str(item or "")
    return {w for w in re.findall(r"[a-z]{4,}", text.casefold())
            if w not in _IGNORE_WORDS}


_IGNORE_WORDS = {"system", "shall", "user", "users", "the", "and", "with",
                 "that", "this", "from", "into", "must", "able", "allow",
                 "allows", "when", "their", "they", "page", "data"}


def _is_a_different_thing(incoming, existing) -> bool:
    """Do these two share so little that one cannot be an edit of the other?"""
    if not (isinstance(incoming, dict) and isinstance(existing, dict)):
        return False
    if not any(str(incoming.get(k, "")).strip() for k in _ID_KEYS):
        return False
    a, b = _words(incoming), _words(existing)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) < 0.25


def _renumbered(item: dict, existing: list) -> dict:
    """The entry with the next free id in the series it was numbered in."""
    key = next((k for k in _ID_KEYS if str(item.get(k, "")).strip()), None)
    if key is None:
        return item
    shape = _ID_SHAPE.match(str(item[key]).strip())
    if not shape:
        return item
    prefix, digits = shape.group(1), shape.group(2)
    highest = 0
    for other in existing:
        if not isinstance(other, dict):
            continue
        got = _ID_SHAPE.match(str(other.get(key, "")).strip())
        if got and got.group(1).casefold() == prefix.casefold():
            highest = max(highest, int(got.group(2)))
    return {**item, key: f"{prefix}{str(highest + 1).zfill(len(digits))}"}


def _clean_patch(patch: dict | None, doc: dict | None = None, prompt: str = "") -> dict:
    """Drop empty or protected model sections without shrinking."""
    doc = doc or {}
    removing = bool(_REMOVAL.search(prompt or ""))
    clean: dict = {}
    for key, value in (patch or {}).items():
        if key in _DERIVED or _blank(value):
            continue
        current = doc.get(key)
        if isinstance(value, list) and isinstance(current, list):
            clean[key] = _merge_list(value, current, removing)
        elif isinstance(value, dict) and isinstance(current, dict):
            section = dict(current)
            for inner, item in value.items():
                current_item = current.get(inner)
                if isinstance(item, list) and isinstance(current_item, list):
                    section[inner] = _merge_list(item, current_item, removing)
                elif not _blank(item):
                    section[inner] = item
            clean[key] = section
        else:
            clean[key] = value
    return clean


def merge_edit(srs: dict, patch: dict | None, prompt: str = "") -> dict:
    """Overlay a section patch onto the current SRS, leaving the rest alone."""
    merged = copy.deepcopy(srs or {})
    doc = merged.setdefault("srs_document", {})
    doc.update(_clean_patch(patch, doc, prompt))
    return merged


def _next_id(items: list[dict], prefix: str) -> str:
    nums = [int(re.sub(r"\D", "", i.get("id", "")) or 0) for i in items]
    return f"{prefix}-{(max(nums) + 1) if nums else 1:03d}"


def apply_customization(srs: dict, prompt: str) -> tuple[dict, list[str]]:
    """Deterministic intent-based editor."""
    doc = copy.deepcopy(srs)["srs_document"]
    p = prompt.lower().strip()
    diff: list[str] = []

    m = re.search(r"rename\s+([\w\- ]+?)\s+(?:to|->|→)\s+([\w\- ]+)", prompt, flags=re.IGNORECASE)
    if m:
        old, new = m.group(1).strip(), m.group(2).strip()
        blob = json.dumps(doc)

        new_blob = re.sub(re.escape(old), new, blob, flags=re.IGNORECASE)
        doc = json.loads(new_blob)
        diff.append(f"Renamed '{old}' → '{new}' across the SRS.")

    if ("performance" in p or "p95" in p or "faster" in p or "stricter" in p) and "rename" not in p:
        changed = False
        for nfr in doc.get("non_functional_requirements", []):
            if nfr.get("category", "").lower() == "performance":
                nfr["requirement"] = re.sub(r"\b3\s*seconds?\b", "1 second", nfr["requirement"], flags=re.IGNORECASE)
                if "P95" not in nfr["requirement"]:
                    nfr["requirement"] += " (P95 latency target tightened)."
                changed = True
        if changed:
            diff.append("Tightened performance NFR to a stricter P95 target.")

    langs = re.findall(r"\b(sinhala|tamil|english|spanish|french|arabic|german|hindi)\b", p)
    if "language" in p or langs:
        names = sorted({l.capitalize() for l in langs}) or ["additional languages"]
        ui = doc.setdefault("ui_ux_requirements", {})
        ui["languages"] = sorted(set(ui.get("languages", []) + names))
        doc.setdefault("functional_requirements", []).append({
            "id": _next_id(doc.get("functional_requirements", []), "FR"),
            "module": "Localization",
            "requirement": f"The system shall support {', '.join(names)} as selectable display languages.",
            "priority": "medium",
        })
        diff.append(f"Added language support: {', '.join(names)}.")

    if "currency" in p or "multi-currency" in p or "multicurrency" in p:
        doc.setdefault("functional_requirements", []).append({
            "id": _next_id(doc.get("functional_requirements", []), "FR"),
            "module": "Payments",
            "requirement": "The system shall support multiple currencies with configurable exchange rates and per-transaction currency selection.",
            "priority": "high",
        })
        diff.append("Added multi-currency requirement.")
    elif "payment" in p or "payment gateway" in p or "stripe" in p or "checkout" in p:
        ints = doc.setdefault("integration_requirements", [])
        if not any(i.get("type") == "payment" for i in ints):
            ints.append({"name": "Payment Gateway", "type": "payment",
                         "description": "Online card payments via a hosted gateway.", "required": True})
        doc.setdefault("functional_requirements", []).append({
            "id": _next_id(doc.get("functional_requirements", []), "FR"),
            "module": "Payments",
            "requirement": "The system shall accept online payments through a secure hosted payment gateway.",
            "priority": "high",
        })
        diff.append("Added online payment gateway requirement.")

    m2 = re.search(r"add\s+(?:admin\s+)?approval\s+for\s+([\w\- ]+)", p)
    if m2:
        thing = m2.group(1).strip()
        doc.setdefault("business_workflows", []).append({
            "workflow_name": f"{thing.title()} Approval Workflow",
            "steps": [f"User requests {thing}", "Request enters 'pending approval' state",
                      "Admin reviews the request", "Admin approves or rejects",
                      f"On approval the {thing} is processed", "Requester is notified of the outcome"],
        })
        doc.setdefault("validation_rules", []).append(
            {"field": thing.replace(" ", "_"), "rule": f"A {thing} requires admin approval before it is finalised."})
        diff.append(f"Added admin approval workflow for {thing}.")

    if "loyalty" in p or "rewards" in p or "points" in p:
        mods = doc.setdefault("main_modules", [])
        if "Loyalty & Rewards" not in mods:
            mods.append("Loyalty & Rewards")
        tables = doc.setdefault("database_design", {}).setdefault("tables", [])
        if not any(t["table_name"] == "loyalty_accounts" for t in tables):
            tables.append({
                "table_name": "loyalty_accounts", "description": "Customer loyalty points balance.",
                "fields": [
                    {"name": "id", "type": "uuid", "primary_key": True},
                    {"name": "user_id", "type": "foreign_key", "references": "users.id"},
                    {"name": "points_balance", "type": "integer", "default": 0},
                    {"name": "tier", "type": "enum", "values": ["bronze", "silver", "gold"], "default": "bronze"},
                ],
            })
        doc.setdefault("functional_requirements", []).append({
            "id": _next_id(doc.get("functional_requirements", []), "FR"),
            "module": "Loyalty & Rewards",
            "requirement": "The system shall award and redeem loyalty points and assign membership tiers.",
            "priority": "medium",
        })
        diff.append("Added loyalty programme (module, table, requirement).")

    if not diff:
        m3 = re.search(r"add\s+(?:a\s+|an\s+)?(.+?)(?:\s+(?:feature|module|requirement|support))?$", p)
        label = (m3.group(1).strip() if m3 else prompt.strip())[:80]
        module = label.title()
        mods = doc.setdefault("main_modules", [])
        if module not in mods:
            mods.append(module)
        doc.setdefault("functional_requirements", []).append({
            "id": _next_id(doc.get("functional_requirements", []), "FR"),
            "module": module,
            "requirement": f"The system shall provide {label}.",
            "priority": "medium",
        })
        diff.append(f"Added requirement & module for: {label}.")

    return {"srs_document": doc}, diff


async def customize_node(state: AgentState) -> AgentState:
    pid = state["project_id"]
    prompt = state.get("customization_prompt", "")
    srs = state.get("srs", {})
    selected_language = "English"
    await bus.log(pid, "CustomizationAgent", f"Applying edit: “{prompt}”", progress=15)

    new_srs, diff = None, []
    try:
        llm = get_llm()
        view = json.dumps(_editable_view(srs))[:_VIEW_BUDGET]
        words = customer_context(brief=state.get("brief", ""),
                                 session=state.get("session"),
                                 project=state.get("project"))
        preamble = f"{words}\n\n" if words else ""
        language_rule = output_language_instruction(
            selected_language,
            artifact="customized software requirements specification",
        ) + ("\nOUTPUT LANGUAGE: Return every customer-visible changed SRS value in English.\n")
        data = await llm.complete_json(
            system=_SYS + language_rule,
            user=f"{preamble}CURRENT SRS:\n{view}\n\nUSER EDIT REQUEST:\n{prompt}",

            validator=lambda d: validate_srs(merge_edit(srs, d.get("srs_document"), prompt)),
            label="srs_customize", trace_sink=lambda pl: bus.trace(pid, pl),
        )
        patch = _clean_patch(data.get("srs_document"), srs.get("srs_document"), prompt)
        if patch:
            new_srs = merge_edit(srs, patch, prompt)
            diff = data.get("diff_summary") or ["Applied requested edit via LLM."]
            await bus.emit(pid, "CustomizationAgent",
                           f"LLM applied edit and validated: {', '.join(sorted(patch))}.",
                           level="success", progress=45)
        else:
            if not is_english(selected_language):
                raise RuntimeError(
                    f"the selected SRS model returned no usable {selected_language} edit"
                )
            await bus.emit(pid, "CustomizationAgent",
                           "LLM changed no section; using deterministic customizer.",
                           level="warn", progress=45)
            new_srs, diff = apply_customization(srs, prompt)
    except (LLMUnavailable, LLMRepairFailed) as exc:
        if not is_english(selected_language):
            await bus.error(pid, "CustomizationAgent",
                            f"The selected SRS model could not apply the edit in "
                            f"{selected_language}: {exc}")
            raise LanguageConversionError(
                f"The selected SRS model could not apply the edit in "
                f"{selected_language}. Check that the selected model is available and try again."
            ) from exc
        await bus.emit(pid, "CustomizationAgent",
                       "Using deterministic customizer." if isinstance(exc, LLMUnavailable)
                       else "LLM edit failed validation; using deterministic customizer.",
                       level="warn", progress=45)
        new_srs, diff = apply_customization(srs, prompt)
    except Exception as exc:  # noqa: BLE001
        if not is_english(selected_language):
            await bus.error(pid, "CustomizationAgent",
                            f"The selected SRS model could not apply the edit in "
                            f"{selected_language}: {exc}")
            raise LanguageConversionError(
                f"The selected SRS model could not apply the edit in "
                f"{selected_language}. Try again with the selected model."
            ) from exc
        await bus.error(pid, "CustomizationAgent", f"Customization error: {exc}; using deterministic editor.")
        new_srs, diff = apply_customization(srs, prompt)

    document = new_srs.get("srs_document")
    if isinstance(document, dict):
        document["document_language"] = "English"
        document["effective_plan"] = plan_srs.effective_plan(document)
        apply_international_profile(new_srs)

    await bus.emit(pid, "CustomizationAgent", f"Edit applied: {len(diff)} change(s).",
                   level="success", progress=55, data={"diff": diff})
    return {**state, "srs": new_srs, "diff_summary": diff}
