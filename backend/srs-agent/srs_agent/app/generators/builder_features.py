"""Capability contracts derived from SRS traceability."""
from __future__ import annotations

from .builder_utils import _clean


def build_feature_contracts(*, requirements: list[dict], pages: list[dict],
                            apis: list[dict], workflows: list[dict],
                            acceptance: list[dict]) -> list[dict]:
    page_lookup = {}
    for page in pages:
        for key in (page.get("name"), page.get("route"), page.get("srs_route")):
            if key:
                page_lookup[str(key).casefold()] = page

    acceptance_by_id = {
        _clean(row.get("id") or ""): _clean(row.get("criterion") or "")
        for row in acceptance if isinstance(row, dict) and _clean(row.get("id") or "")
    }

    out = []
    for req in requirements:
        rid = _clean(req.get("source_id") or req.get("id") or "")
        proof = req.get("proof") or {}
        target_pages = []
        routes = []
        roles = [_clean(x) for x in (req.get("roles") or []) if _clean(x)]
        for raw in proof.get("pages") or []:
            page = page_lookup.get(str(raw).casefold())
            if not page:
                continue
            name = page.get("name") or raw
            if name not in target_pages:
                target_pages.append(name)
            route = page.get("route")
            if route and route not in routes:
                routes.append(route)
            for role in page.get("allowed_roles") or []:
                if role not in roles:
                    roles.append(role)

        related_workflows = []
        for flow in workflows:
            flow_routes = set(flow.get("routes") or [])
            if routes and flow_routes.intersection(routes):
                related_workflows.append(flow.get("name"))
                if flow.get("who") and flow.get("who") not in roles:
                    roles.append(flow.get("who"))

        api_targets = []
        req_low = _clean(req.get("text") or "").lower()
        for ep in apis:
            desc = _clean(ep.get("description") or "").lower()
            path = str(ep.get("path") or "")
            data_words = [str(x).replace("_", " ").lower() for x in (proof.get("tables") or [])]
            if any(word and (word in desc or word.rstrip("s") in path.lower()) for word in data_words):
                api_targets.append(f"{ep.get('method')} {path}")
            elif req_low and desc and any(token in desc for token in req_low.split() if len(token) > 5):
                api_targets.append(f"{ep.get('method')} {path}")

        acceptance_text = acceptance_by_id.get(rid)
        out.append({
            "id": "CAP-" + (rid or str(len(out) + 1)),
            "requirement_id": rid or None,
            "requirement": _clean(req.get("text") or ""),
            "kind": req.get("kind") or "feature",
            "module": req.get("module"),
            "roles": roles,
            "pages": target_pages,
            "routes": routes,
            "apis": api_targets,
            "data": list(proof.get("tables") or []),
            "workflows": [x for x in related_workflows if x],
            "acceptance": acceptance_text or proof.get("test_case") or None,
            "e2e_required": bool(routes),
        })
    return out


def feature_prompt_lines(contracts: list[dict]) -> list[str]:
    if not contracts:
        return []
    lines = ["FEATURE LEDGER — EACH ROW MUST BECOME WORKING CODE:"]
    for cap in contracts:
        target = []
        if cap.get("roles"):
            target.append("roles=" + ",".join(cap["roles"]))
        if cap.get("routes"):
            target.append("routes=" + ",".join(cap["routes"]))
        if cap.get("apis"):
            target.append("apis=" + ",".join(cap["apis"]))
        if cap.get("data"):
            target.append("data=" + ",".join(cap["data"]))
        if cap.get("workflows"):
            target.append("journey=" + ",".join(cap["workflows"]))
        suffix = " | " + "; ".join(target) if target else ""
        lines.append(f"- {cap.get('id')}: {cap.get('requirement')}{suffix}")
        if cap.get("acceptance"):
            lines.append(f"    proof: {cap.get('acceptance')}")
    lines += [
        "- Do not declare a capability complete because its page exists. Its action, persistence, access rule and next visible state must work.",
        "",
    ]
    return lines
