"""Plan merge, journey completion and review rendering."""
from __future__ import annotations

def _open_question(raw) -> dict:
    """One open question, with the answers it will accept."""
    if not isinstance(raw, dict):
        return {"question": str(raw).strip(), "required": False, "options": []}

    seen: list[str] = []
    for opt in raw.get("options") or []:
        text = str(opt.get("label") if isinstance(opt, dict) else opt).strip()
        if text and text not in seen:
            seen.append(text)

    return {
        "question": str(raw.get("question", "")).strip(),
        "required": bool(raw.get("required")),
        # Four is the point past which reading them costs more.
        "options": seen[:4],
    }


def _merge(skeleton: dict, plan: dict) -> dict:
    """The model's plan, with the skeleton filling anything it left empty."""
    out = dict(skeleton)
    # App renames are ordinary plan revisions.
    for key in ("app_name", "product_intent", "look_and_feel"):
        value = str(plan.get(key) or "").strip()
        if value:
            out[key] = value
    policy = plan.get("account_policy")
    if isinstance(policy, dict) and policy:
        out["account_policy"] = policy
    for key in ("users", "screens", "records", "workflows", "features",
                "assumptions"):
        value = plan.get(key)
        if isinstance(value, list) and value:
            out[key] = value

    # An empty open-question list is a valid answer.
    asked = plan.get("open_questions")
    if isinstance(asked, list):
        out["open_questions"] = asked
    out["open_questions"] = [
        _open_question(q) for q in (out.get("open_questions") or [])
        if str(q.get("question") if isinstance(q, dict) else q).strip()
    ]

    out["customer_notes"] = str(skeleton.get("customer_notes") or "")
    out["workflows"] = _journeys_for_every_role(out)
    return out


def _journeys_for_every_role(plan: dict) -> list[dict]:
    """The plan's workflows as user journeys."""
    roles = [str(u.get("role") or "").strip() for u in (plan.get("users") or [])
             if isinstance(u, dict) and str(u.get("role") or "").strip()]
    flows: list[dict] = []
    for w in plan.get("workflows") or []:
        if not isinstance(w, dict):
            continue
        steps = [str(s).strip() for s in (w.get("steps") or []) if str(s).strip()]
        if not steps:
            continue
        who = str(w.get("who") or "").strip()
        if not who and roles:
            blob = " ".join([str(w.get("name") or "")] + steps).lower()
            who = next((r for r in roles if r.lower() in blob), "")
        flows.append({"name": str(w.get("name") or "Journey").strip(),
                      "who": who, "steps": steps})

    covered = {f["who"].lower() for f in flows if f["who"]}
    for u in plan.get("users") or []:
        if not isinstance(u, dict):
            continue
        role = str(u.get("role") or "").strip()
        can = [str(c).strip() for c in (u.get("can_do") or []) if str(c).strip()]
        if not role or role.lower() in covered or not can:
            continue
        needs_account = bool((plan.get("account_policy") or {}).get("accounts_required"))
        steps = ([f"{role} signs in"] if needs_account else []) + can
        flows.append({"name": f"What the {role.lower()} does here", "who": role,
                      "steps": steps})
        covered.add(role.lower())
    return flows


def render_plan_markdown(plan: dict, *, app_name: str = "") -> str:
    """The plan as Markdown, for the review screen."""
    out: list[str] = []
    if app_name:
        out += [f"# {app_name}", ""]
    out += [str(plan.get("product_intent") or "").strip(), ""]

    notes = str(plan.get("customer_notes") or "").strip()
    if notes:
        out += ["## In your own words", ""]
        out += [line for line in notes.splitlines() if line.strip()]
        out.append("")

    users = plan.get("users") or []
    if users:
        out.append("## Who uses it")
        out.append("")
        for u in users:
            out.append(f"**{u.get('role', 'User')}**")
            for item in u.get("can_do") or []:
                out.append(f"- {item}")
            out.append("")

    policy = plan.get("account_policy") or {}
    if policy.get("accounts_required"):
        out += ["## Account access", ""]
        out.append(f"- Sign in: {policy.get('sign_in_route') or 'required'} using " + ", ".join(policy.get("sign_in_fields") or []))
        if policy.get("registration_mode") == "open":
            out.append(f"- Public sign-up: {policy.get('sign_up_route') or 'required'} → {policy.get('registration_role')}")
        else:
            out.append(f"- Public sign-up: no ({policy.get('registration_mode') or 'admin-created'})")
        out.append("")

    screens = plan.get("screens") or []
    if screens:
        out += ["## Screens", "", "| Screen | What it is for | Who sees it |",
                "| --- | --- | --- |"]
        for s in screens:
            who = ", ".join(s.get("who") or []) or "Everyone"
            out.append(f"| {s.get('name', '')} | {s.get('purpose', '')} | {who} |")
        out.append("")

    records = plan.get("records") or []
    if records:
        out += ["## What it keeps track of", ""]
        for r in records:
            keeps = ", ".join(r.get("keeps") or []) or "—"
            out.append(f"- **{r.get('name', '')}** — {keeps}")
        out.append("")

    workflows = plan.get("workflows") or []
    if workflows:
        out += ["## User journeys", "",
                "How each kind of person moves through the app, start to "
                "finish — the pages they pass through and what happens on "
                "each. Every journey here is built to be walkable end to end, "
                "and it is what the finished app is tested against.", ""]
        for w in workflows:
            who = str(w.get("who") or "").strip()
            out.append(f"**{w.get('name', 'Journey')}**"
                       + (f" — {who}" if who else ""))
            for i, step in enumerate(w.get("steps") or [], start=1):
                out.append(f"{i}. {step}")
            out.append("")

    features = plan.get("features") or []
    if features:
        out += ["## What it does", ""]
        out += [f"- {f}" for f in features]
        out.append("")

    if plan.get("look_and_feel"):
        out += ["## Look and feel", "", str(plan["look_and_feel"]), ""]

    assumptions = plan.get("assumptions") or []
    if assumptions:
        out += ["## Things we assumed", ""]
        out += [f"- {a}" for a in assumptions]
        out.append("")

    open_qs = plan.get("open_questions") or []
    if open_qs:
        out += ["## Still to settle", ""]
        for q in open_qs:
            mark = " **(needed before we can write the SRS)**" if q.get("required") else ""
            out.append(f"- {q.get('question', '')}{mark}")
        out.append("")

    return "\n".join(out).strip() + "\n"
