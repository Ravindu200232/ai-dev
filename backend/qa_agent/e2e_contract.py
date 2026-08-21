"""Machine-readable proof contract for each browser journey."""
from __future__ import annotations

import re
from pathlib import Path

from .e2e_verbs import journey_mutates


_PUBLIC_ACTORS = {"", "public", "signed-out", "signed out", "anonymous", "visitor", "none", "nobody", "logged-out", "logged out"}


def _actor_class(value: str) -> str:
    """Map equivalent unauthenticated labels to one contract actor."""
    role = str(value or "").strip().lower()
    return "public" if role in _PUBLIC_ACTORS else role


def _clean_list(values, cap=16):
    out = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= cap:
            break
    return out


def _shipped_files(arch, wanted) -> tuple[list, list]:
    """Split planned source files into the ones that were really generated."""
    files = getattr(arch, "files", None) or {}
    try:
        root = Path(getattr(arch, "project_dir", "") or ".")
    except Exception:
        root = Path(".")
    present, missing = [], []
    for rel in wanted:
        rel = str(rel or "").strip().replace("\\", "/")
        if not rel:
            continue
        here = rel in files
        if not here:
            try:
                here = (root / rel).is_file()
            except Exception:
                here = False
        (present if here else missing).append(rel)
    return present, missing


def refresh_shipped_files(arch, contract: dict) -> dict:
    """Re-check a frozen contract's files against the app as it is right now."""
    if not contract:
        return contract
    wanted = list(contract.get("source_files") or []) + \
        list(contract.get("missing_source_files") or [])
    if not wanted:
        return contract
    present, absent = _shipped_files(arch, wanted)
    contract["source_files"] = present
    contract["missing_source_files"] = absent
    return contract


def capability_contract(arch, journey: dict) -> dict:
    """Compile workflow/capability/contract facts into one proof ledger."""
    plan = getattr(arch, "plan", None) or {}
    covers = {str(x or "").strip().upper() for x in (journey.get("covers") or [])
              if str(x or "").strip()}
    actor = _actor_class(str(journey.get("role") or ""))
    declared = [c for c in (plan.get("capabilities") or []) if isinstance(c, dict)]
    if covers:
        # The architect paired these ids with this journey on purpose.
        caps = [c for c in declared
                if str(c.get("id") or "").strip().upper() in covers]
    else:
        # `not covers` used to short-circuit the id test
        caps = [c for c in declared
                if not str(c.get("who") or "").strip()
                or _actor_class(str(c.get("who") or "")) == actor]

    proofs, files, requirements = [], [], []
    for cap in caps:
        requirements.extend([cap.get("requirement")])
        proofs.extend([cap.get("proof")])
        files.extend(cap.get("files") or [])

    handoffs = []
    for item in (plan.get("contracts") or []):
        if not isinstance(item, dict):
            continue
        frm = str(item.get("from") or "").strip()
        target = str(item.get("target") or "").strip()
        if files and frm not in files and not any(f and f in target for f in files):
            continue
        effect = str(item.get("effect") or "").strip()
        trigger = str(item.get("trigger") or "").strip()
        if trigger or effect:
            handoffs.append({"from": frm, "target": target,
                             "trigger": trigger, "effect": effect})

    steps = _clean_list(journey.get("steps") or [], 20)
    text = " ".join(steps + _clean_list(requirements) + _clean_list(proofs))
    shipped, absent = _shipped_files(arch, _clean_list(files))
    return {
        "title": str(journey.get("title") or "").strip(),
        "actor": actor,
        "requires_session": actor != "public",
        "workflow_steps": steps,
        "requirements": _clean_list(requirements),
        "proofs": _clean_list(proofs),
        "source_files": shipped,
        "missing_source_files": absent,
        "handoffs": handoffs[:10],
        # The capabilities this journey covers decide it.
        "expects_mutation": journey_mutates(caps if covers else [], steps),
        "preserve_auth": not bool(re.search(r"\b(login|log in|sign in|authenticate|session|role)\b", text, re.I)),
    }


def scenario_contract_issue(contract: dict, scenario, is_business_step) -> str:
    """Reject a scenario that cannot prove the contract even if selectors parse."""
    if not contract:
        return ""
    expected_role = _actor_class(contract.get("actor"))
    actual_role = _actor_class(getattr(scenario, "role", ""))
    if expected_role and actual_role != expected_role:
        return f"scenario role {actual_role!r} does not match required actor {expected_role!r}"

    steps = list(getattr(scenario, "steps", []) or [])
    if not contract.get("expects_mutation"):
        return ""
    business = [i for i, step in enumerate(steps) if is_business_step(step)]
    if not business:
        return "the capability contract requires a business mutation, but the scenario never performs one"
    first = business[0]
    proof_verbs = {"EXPECT_TEXT", "EXPECT_URL", "EXPECT_VALUE", "WAIT_FOR", "EXPECT_NO_ERROR"}
    if not any(getattr(step, "verb", "") in proof_verbs for step in steps[first + 1:]):
        return "the scenario performs a business action but never proves its resulting state"
    return ""


def runtime_contract_issue(contract: dict, scenario, evidence: dict,
                           is_business_step) -> str:
    """Require an observed persisted effect for contracts that mutate data."""
    if not contract or not contract.get("expects_mutation"):
        return ""
    steps = list(getattr(scenario, "steps", []) or [])
    business = [i for i, step in enumerate(steps) if is_business_step(step)]
    if not business:
        return "the contract requires a mutation but the scenario has no business action"
    first = business[0]
    mutations = []
    for row in (evidence or {}).get("mutation_events") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if "/api/auth/" in url:
            continue
        try:
            idx = int(row.get("step_index", -1))
        except Exception:
            idx = -1
        if idx >= first and int(row.get("status") or 0) < 300:
            mutations.append(row)
    if not mutations:
        return ("the capability contract requires a persisted business change, "
                "but the browser observed no successful non-auth mutation request")
    return ""
