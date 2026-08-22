"""Deterministic unit and E2E inputs derived from the approved SRS."""
from __future__ import annotations

import re

from .builder_utils import _clean


def _unique(values) -> list:
    out = []
    for value in values or []:
        if value in (None, "") or value in out:
            continue
        out.append(value)
    return out


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [_clean(value)] if _clean(value) else []
    return [_clean(item) for item in (value or []) if _clean(item)]


def _case_id(prefix: str, value: str, number: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9-]+", "-", str(value or "")).strip("-")
    return f"{prefix}-{safe or f'{number:03d}'}"


def _cap_refs(values, contracts: list[dict]) -> list[str]:
    """Resolve FR ids and capability ids to canonical capability ids."""
    lookup = {}
    for cap in contracts:
        cid = _clean(cap.get("id") or "")
        rid = _clean(cap.get("requirement_id") or "")
        if cid:
            lookup[cid.casefold()] = cid
        if rid:
            lookup[rid.casefold()] = cid
    return _unique(lookup.get(value.casefold(), value) for value in _strings(values))


def _target_files(cap: dict, pages: list[dict], apis: list[dict]) -> list[str]:
    page_by_route = {str(page.get("route") or ""): page for page in pages}
    page_by_name = {str(page.get("name") or "").casefold(): page for page in pages}
    api_by_key = {
        f"{str(ep.get('method') or 'GET').upper()} {ep.get('path')}": ep
        for ep in apis
    }
    files = []
    for route in cap.get("routes") or []:
        page = page_by_route.get(str(route))
        if page and page.get("expected_file"):
            files.append(page["expected_file"])
    for name in cap.get("pages") or []:
        page = page_by_name.get(str(name).casefold())
        if page and page.get("expected_file"):
            files.append(page["expected_file"])
    for key in cap.get("apis") or []:
        ep = api_by_key.get(str(key))
        if ep and ep.get("expected_file"):
            files.append(ep["expected_file"])
    return _unique(files)


def _unit_cases(*, feature_contracts: list[dict], pages: list[dict],
                apis: list[dict], validations: list[dict],
                auth_contract: dict) -> list[dict]:
    cases = []
    auth_enabled = bool(auth_contract.get("enabled"))
    for number, cap in enumerate(feature_contracts, 1):
        cid = _clean(cap.get("id") or "")
        roles = _strings(cap.get("roles"))
        kinds = ["success"]
        if cap.get("kind") in {"create", "edit", "delete"} or cap.get("apis"):
            kinds.extend(["invalid_input", "dependency_failure"])
        if auth_enabled and roles:
            kinds.append("signed_out_rejected")
            kinds.append("wrong_role_forbidden")
        assertion = _clean(cap.get("acceptance") or "")
        if not assertion:
            assertion = "Durably and visibly prove: " + _clean(cap.get("requirement") or cid)
        cases.append({
            "id": _case_id("UT", cap.get("requirement_id") or cid, number),
            "covers": [cid] if cid else [],
            "requirement": _clean(cap.get("requirement") or ""),
            "targets": _target_files(cap, pages, apis),
            "planner_must_assign_target": not bool(_target_files(cap, pages, apis)),
            "arrange": {
                "session": ("authenticated_as_one_of: " + ", ".join(roles))
                if auth_enabled and roles else "public_or_not_applicable",
                "data_fixtures": _strings(cap.get("data")),
                "validation_rules": list(validations),
            },
            "act": _clean(cap.get("requirement") or cid),
            "assert": [assertion],
            "cases": _unique(kinds),
        })
    return cases


def _pre_journey(*, flow: dict, caps: list[dict], auth_contract: dict) -> dict:
    routes = _unique(flow.get("routes") or [])
    apis = _unique(api for cap in caps for api in (cap.get("apis") or []))
    entities = _unique(entity for cap in caps for entity in (cap.get("data") or []))
    roles = _unique([flow.get("who")] + [role for cap in caps for role in (cap.get("roles") or [])])
    role = roles[0] if roles else None
    protected = bool(role) or any(
        route in {rule_route for row in (auth_contract.get("roles") or [])
                  for rule_route in (row.get("allowed_routes") or [])}
        for route in routes
    )
    sign_in_required = bool(auth_contract.get("enabled") and protected)
    dynamic_routes = [route for route in routes if "[" in str(route)]
    dynamic_apis = [api for api in apis if "[" in str(api)]
    record_required = bool(dynamic_routes or dynamic_apis or any(
        cap.get("kind") in {"list", "edit", "delete"} for cap in caps
    ))
    records = []
    if record_required:
        for entity in entities or ["the journey record"]:
            records.append({
                "entity": entity,
                "minimum": 1,
                "stable_real_id": bool(dynamic_routes or dynamic_apis),
                "reason": ("resolve a dynamic journey route/API with a real seeded id"
                           if dynamic_routes or dynamic_apis
                           else "provide deterministic visible data for the journey"),
            })
    explicit = _strings(flow.get("preconditions"))
    return {
        "database": "fresh_deterministic_seed",
        "explicit_preconditions": explicit,
        "account": {
            "sign_in_required": sign_in_required,
            "role": role or "visitor",
            "fixture": ("one distinct seeded account for this exact role"
                        if sign_in_required else "no account required"),
            "identity_fields": (list(auth_contract.get("identity_fields") or [])
                                if sign_in_required else []),
            "sign_in_route": auth_contract.get("sign_in_route") if sign_in_required else None,
            "role_home": (auth_contract.get("role_home") or {}).get(role) if role else None,
            "never_reuse_another_role": bool(sign_in_required),
        },
        "seed_entities": entities,
        "required_records": records,
        "required_routes": routes,
        "required_apis": apis,
        "initial_route": (auth_contract.get("sign_in_route") if sign_in_required
                          else (routes[0] if routes else "/")),
        "depends_on_prior_e2e": False,
    }


def _matching_caps(flow: dict, contracts: list[dict]) -> list[dict]:
    explicit = set(_cap_refs(flow.get("covers") or [], contracts))
    name = _clean(flow.get("name") or "").casefold()
    routes = set(flow.get("routes") or [])
    matches = []
    for cap in contracts:
        cid = _clean(cap.get("id") or "")
        cap_flows = {_clean(value).casefold() for value in (cap.get("workflows") or [])}
        if (cid in explicit or (name and name in cap_flows)
                or routes.intersection(cap.get("routes") or [])):
            matches.append(cap)
    return matches


def _e2e_case(*, flow: dict, caps: list[dict], number: int,
              auth_contract: dict, synthesized: bool = False) -> dict:
    proofs = _unique(
        _strings(flow.get("expected_results"))
        + [_clean(cap.get("acceptance") or "") for cap in caps if _clean(cap.get("acceptance") or "")]
        + ["Durably and visibly prove: " + _clean(cap.get("requirement") or "")
           for cap in caps if not _clean(cap.get("acceptance") or "")]
    )
    required_actions = _unique(cap.get("requirement") for cap in caps)
    return {
        "id": f"E2E-{number:03d}",
        "name": _clean(flow.get("name") or f"Journey {number}"),
        "actor": _clean(flow.get("who") or "") or "visitor",
        "source": "synthesized_from_uncovered_srs_capability" if synthesized else flow.get("source"),
        "synthesized": synthesized,
        "covers": _unique(cap.get("id") for cap in caps),
        "routes": _unique(flow.get("routes") or []),
        "pre_journey": _pre_journey(flow=flow, caps=caps, auth_contract=auth_contract),
        "steps": _strings(flow.get("steps")),
        "required_actions": required_actions,
        "proofs": proofs or ["The final visible state proves every covered capability"],
        "selector_contract": (
            "Builder assigns stable accessible names to every action and field used here; "
            "use literal data-testid only when an accessible selector cannot be stable."
        ),
    }


def build_testing_contract(*, feature_contracts: list[dict], workflows: list[dict],
                           pages: list[dict], apis: list[dict],
                           validations: list[dict], auth_contract: dict) -> dict:
    """Give Builder complete, deterministic test inputs before QA begins."""
    unit = _unit_cases(
        feature_contracts=feature_contracts, pages=pages, apis=apis,
        validations=validations, auth_contract=auth_contract,
    )
    e2e = []
    covered = set()
    for flow in workflows:
        caps = _matching_caps(flow, feature_contracts)
        if not caps:
            continue
        case = _e2e_case(
            flow=flow, caps=caps, number=len(e2e) + 1,
            auth_contract=auth_contract,
        )
        e2e.append(case)
        covered.update(case["covers"])

    browser_caps = [cap for cap in feature_contracts if cap.get("e2e_required")]
    synthesized = []
    for cap in browser_caps:
        cid = cap.get("id")
        if cid in covered:
            continue
        role = (cap.get("roles") or [None])[0]
        routes = list(cap.get("routes") or [])
        proof = _clean(cap.get("acceptance") or "")
        flow = {
            "name": f"Proof for {cid}",
            "who": role,
            "covers": [cid],
            "routes": routes,
            "steps": ([f"Open {routes[0]}"] if routes else []) + [
                _clean(cap.get("requirement") or cid),
                proof or f"Verify the durable visible result for {cid}",
            ],
            "expected_results": [proof] if proof else [],
            "preconditions": [],
        }
        case = _e2e_case(
            flow=flow, caps=[cap], number=len(e2e) + 1,
            auth_contract=auth_contract, synthesized=True,
        )
        e2e.append(case)
        synthesized.append(case["id"])
        covered.add(cid)

    unit_covered = {cid for case in unit for cid in case.get("covers") or []}
    browser_ids = {cap.get("id") for cap in browser_caps}
    return {
        "contract_version": 1,
        "unit": unit,
        "e2e": e2e,
        "shared_validation_cases": list(validations),
        "coverage": {
            "all_capabilities": [cap.get("id") for cap in feature_contracts],
            "browser_capabilities": [cap.get("id") for cap in browser_caps],
            "requirements_without_unit": [
                cap.get("id") for cap in feature_contracts if cap.get("id") not in unit_covered
            ],
            "browser_capabilities_without_e2e": sorted(browser_ids - covered),
            "synthesized_e2e": synthesized,
        },
        "rules": [
            "Builder implements every pre_journey fixture before handing the app to QA.",
            "Each E2E journey starts from a fresh deterministic seed and is independent unless the SRS explicitly declares a dependency.",
            "Use one distinct seeded identity per role; never authenticate a role journey with another role's account.",
            "Dynamic routes use real seeded ids and required parent/reference records.",
            "Unit coverage includes success, validation, dependency failure, signed-out and wrong-role cases whenever applicable.",
            "A passing test must prove durable product behavior; never fake, skip or weaken a test to make the handoff pass.",
        ],
    }


def testing_prompt_lines(contract: dict) -> list[str]:
    """Render the structured testing contract without losing its setup details."""
    if not contract:
        return []
    lines = ["TEST-READY BUILD INPUT — BUILDER OWNS THIS BEFORE QA:"]
    lines.append("- Build the code, fixtures and stable UI needed by these contracts. Pre-journey setup is implementation work, not something QA should guess.")
    lines.append("UNIT TEST CONTRACT:")
    for case in contract.get("unit") or []:
        targets = ",".join(case.get("targets") or []) or "planner must assign an exact implementation file"
        lines.append(f"- {case.get('id')} covers={','.join(case.get('covers') or [])} targets={targets}")
        lines.append(f"    arrange: session={case.get('arrange', {}).get('session')}; data={','.join(case.get('arrange', {}).get('data_fixtures') or []) or 'none'}")
        lines.append(f"    act: {case.get('act')}")
        lines.append("    assert: " + "; ".join(case.get("assert") or []))
        lines.append("    cases: " + ", ".join(case.get("cases") or []))
    lines.append("")
    lines.append("E2E JOURNEY CONTRACT:")
    for case in contract.get("e2e") or []:
        pre = case.get("pre_journey") or {}
        account = pre.get("account") or {}
        lines.append(f"- {case.get('id')} {case.get('name')} actor={case.get('actor')} covers={','.join(case.get('covers') or [])}")
        lines.append(
            "    PRE-JOURNEY: "
            f"start={pre.get('initial_route')}; seed={','.join(pre.get('seed_entities') or []) or 'fresh empty database'}; "
            f"role={account.get('role')}; account={account.get('fixture')}; "
            f"routes={','.join(pre.get('required_routes') or []) or 'none'}; "
            f"apis={','.join(pre.get('required_apis') or []) or 'none'}"
        )
        for condition in pre.get("explicit_preconditions") or []:
            lines.append(f"    precondition: {condition}")
        for record in pre.get("required_records") or []:
            stable = str(bool(record.get("stable_real_id"))).lower()
            lines.append(f"    seeded record: {record.get('entity')} minimum={record.get('minimum')} stable_real_id={stable}")
        for step in case.get("steps") or []:
            lines.append(f"    step: {step}")
        for action in case.get("required_actions") or []:
            lines.append(f"    required action: {action}")
        for proof in case.get("proofs") or []:
            lines.append(f"    proof: {proof}")
    lines.append("")
    lines.append("TEST HANDOFF RULES:")
    lines.extend("- " + rule for rule in (contract.get("rules") or []))
    lines.append("")
    return lines


__all__ = ["build_testing_contract", "testing_prompt_lines"]
