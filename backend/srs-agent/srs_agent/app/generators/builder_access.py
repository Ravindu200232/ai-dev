"""Role access and journey routing for builder handoffs."""
from __future__ import annotations

import re

from .builder_utils import _clean, _normal_route_pattern, _strip_forbidden

def _access_section(doc: dict, plan: dict) -> list[str]:
    """Who signs in, what each of them reaches, and where it is enforced."""
    doc = doc or {}
    lines: list[str] = []

    roles: list[str] = []
    for role in doc.get("roles") or []:
        if not isinstance(role, dict):
            continue
        name = _clean(role.get("role_name") or role.get("role_key") or "")
        if not name:
            continue
        why = _clean(_strip_forbidden(role.get("description") or ""))
        roles.append(f"- {name}" + (f" — {why}" if why else ""))
    if not roles:
        for user in plan.get("users") or []:
            name = _clean(user.get("role", ""))
            if not name:
                continue
            can = ", ".join(_clean(c) for c in (user.get("can_do") or []) if _clean(c))
            roles.append(f"- {name}" + (f" — {can}" if can else ""))
    if roles:
        lines += ["WHO SIGNS IN:"] + roles + [""]

    public = [_normal_route_pattern(_clean(p.get("route", ""))) for p in (doc.get("public_pages") or [])
              if isinstance(p, dict) and _clean(p.get("route", ""))]
    if public:
        lines.append("OPEN TO EVERYONE, SIGNED IN OR NOT: "
                     + ", ".join(public) + ".")

    guarded: list[str] = []
    for page in doc.get("protected_pages") or []:
        if not isinstance(page, dict):
            continue
        route = _normal_route_pattern(_clean(page.get("route", "")))
        if not route:
            continue
        allowed = [_clean(r) for r in (page.get("allowed_roles") or []) if _clean(r)]
        guarded.append(f"- {route} — "
                       + (", ".join(allowed) if allowed else "any signed-in user"))
    if guarded:
        lines += ["", "SIGN-IN REQUIRED, AND ONLY THESE ROLES:"] + guarded

    # Show the approved account-creation policy.
    first_role = ""
    for role in doc.get("roles") or []:
        if isinstance(role, dict):
            first_role = _clean(role.get("role_name") or role.get("role_key") or "")
            if first_role:
                break
    if not first_role:
        for user in plan.get("users") or []:
            first_role = _clean(user.get("role", ""))
            if first_role:
                break

    joins = re.compile(r"sign[ -]?up|register|create an account", re.I)
    open_signup = any(
        joins.search(str(p.get("page_name", "")) + " " + str(p.get("route", "")))
        for p in ((doc.get("public_pages") or []) + (doc.get("protected_pages") or []))
        if isinstance(p, dict))

    others = [r.split(" — ")[0].lstrip("- ") for r in roles]
    others = [r for r in others if r and r != first_role]

    lines += ["", "SIGNING UP:"]
    if open_signup:
        lines.append(
            f"- Anyone can create an account, and a new account is a "
            f"{first_role or 'standard user'} — that is the only role sign-up "
            f"ever produces.")
        if others:
            named = (others[0] if len(others) == 1
                     else ", ".join(others[:-1]) + " and " + others[-1])
            lines.append(
                f"- {named} {'is' if len(others) == 1 else 'are'} NOT "
                f"self-service. There is no way to pick a role on the sign-up "
                f"form and no role field on it; "
                f"{'that account is' if len(others) == 1 else 'those accounts are'} "
                f"made by somebody who already has one.")
    else:
        lines.append(
            "- There is NO public sign-up. Do not build a /signup page, a "
            "register form, or a 'create an account' link anywhere. Every "
            "account is made by somebody who already has one, from inside the "
            "app. The sign-in page has no way to register from it.")
    if not open_signup and first_role:
        lines.append(
            f"- The seeded accounts are how anybody gets in at first, "
            f"{first_role} included.")

    denied: list[str] = []
    for entry in doc.get("role_access_matrix") or []:
        if not isinstance(entry, dict):
            continue
        role = _clean(entry.get("role", ""))
        never = [_clean(f) for f in (entry.get("restricted_functions") or [])
                 if _clean(f)]
        if role and never:
            denied.append(f"- {role} must never {never[0][:1].lower()}{never[0][1:]}"
                          + (f" (also: {', '.join(never[1:3])})" if len(never) > 1 else ""))
    if denied:
        lines += ["", "MUST NOT BE ABLE TO:"] + denied

    if lines:
        lines += [
            "",
            "Enforce every line above on the SERVER — in the page or the route "
            "handler, by reading the session and checking the role before "
            "anything is fetched or written. Hiding a link or a button is not "
            "access control: the route is still there and typing it works. A "
            "signed-out visitor asking for a protected route goes to the login "
            "page; a signed-in one without the role gets refused, not the page. "
            "The role lives on the user record and is set when the account is "
            "created — never taken from a form, a query string or a cookie the "
            "browser can write.",
            "",
        ]
    return lines


_LABEL_STOP = {"page", "pages", "screen", "the", "a", "an", "and", "of",
               "for", "to", "in", "on", "my", "your", "view", "all"}


def _label_words(label: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", label.lower())
            if w not in _LABEL_STOP and len(w) >= 3]


def _mentions(word: str, text: str) -> bool:
    """`word` in `text`, tolerant of plurals both ways."""
    stem = word[:-1] if word.endswith("s") and len(word) > 4 else word
    return re.search(rf"\b{re.escape(stem)}(s|es)?\b", text) is not None


def _routes_for(step: str, pages: list[dict], who: str = "",
                roles_of: dict | None = None) -> list[str]:
    """The routes of the pages a workflow step is talking."""
    text = (step or "").lower()
    who = (who or "").lower().strip()
    roles_of = roles_of or {}

    def sees(route: str) -> bool:
        allowed = [r.lower() for r in (roles_of.get(route) or [])]
        return not who or not allowed or who in allowed

    hits: list[tuple[int, int, str]] = []
    for page in pages:
        label = str(page.get("name") or "").lower().strip()
        route = str(page.get("route") or "")
        if label and route and label in text:
            hits.append((text.find(label), -len(label), route))
    if hits:
        hits.sort()
        out: list[str] = []
        for _, _, route in hits:
            if route not in out:
                out.append(route)
        return out

    def stem(w: str) -> str:
        return w[:-1] if w.endswith("s") and len(w) > 4 else w
    # Count only pages this role can reach.
    freq: dict[str, int] = {}
    for page in pages:
        r = str(page.get("route") or "")
        if who and roles_of.get(r) and not sees(r):
            continue
        for w in set(stem(x) for x in _label_words(str(page.get("name") or ""))):
            freq[w] = freq.get(w, 0) + 1

    found: list[tuple[int, float, str]] = []
    for page in pages:
        label = str(page.get("name") or "")
        route = str(page.get("route") or "")
        words = _label_words(label)
        if not words or not route:
            continue
        # Ignore pages the journey role cannot reach.
        if who and roles_of.get(route) and not sees(route):
            continue
        got = [w for w in words if _mentions(w, text)]
        if not got:
            continue
        full = len(got) == len(words)
        head = stem(words[-1])
        head_only = head in {stem(g) for g in got} and freq.get(head, 0) == 1
        if not (full or head_only):
            continue
        score = ((1.0 if full else 0.6) + (0.3 if sees(route) else 0.0)
                 + len(label) * 0.0001)
        pos = min(text.find(stem(g)) for g in got)
        found.append((pos, -score, route))
    if not found:
        return []
    found.sort()
    # Keep matching pages in the order mentioned.
    out = []
    for _, _, route in found:
        if route not in out:
            out.append(route)
    return out


def _route_for(name: str, pages: list[dict], who: str = "",
               roles_of: dict | None = None) -> str:
    """The first route a workflow step is talking about, or ""."""
    routes = _routes_for(name, pages, who, roles_of)
    return routes[0] if routes else ""


def _flow_section(plan: dict, pages: list[dict], doc: dict | None = None) -> list[str]:
    """The road map: where somebody starts and how they get anywhere."""
    if not pages:
        return []

    roles_of: dict[str, list[str]] = {}
    for bucket in ("public_pages", "protected_pages"):
        for page in ((doc or {}).get(bucket) or []):
            if not isinstance(page, dict):
                continue
            name = _clean(str(page.get("page_name") or "")).lower()
            allowed = [_clean(r) for r in (page.get("allowed_roles") or []) if _clean(r)]
            for p in pages:
                if _clean(str(p.get("name") or "")).lower() == name and p.get("route"):
                    roles_of[str(p["route"])] = allowed
    lines = ["USER JOURNEYS — HOW THE PAGES CONNECT:"]
    entry = pages[0]
    lines.append(f"- Somebody arrives at {entry.get('route') or '/'} "
                 f"({entry.get('name')}). Every other page is reached from "
                 f"there, in at most two clicks.")

    for flow in (plan.get("workflows") or []):
        title = _clean(flow.get("name", ""))
        who = _clean(str(flow.get("who") or ""))
        if who:
            title = f"{title} [{who}]"
        steps = [_clean(str(s)) for s in (flow.get("steps") or []) if _clean(str(s))]
        if not title or not steps:
            continue
        chain: list[str] = []
        at: dict[str, list[str]] = {}
        for step in steps:
            routes = _routes_for(step, pages, who, roles_of)
            if routes:
                for route in routes:
                    if not chain or chain[-1] != route:
                        chain.append(route)
                # What happens is written under the LAST page the step.
                at.setdefault(routes[-1], []).append(step)
            elif chain:
                # A step with no page of its own
                at.setdefault(chain[-1], []).append(step)
        if len(chain) >= 2:
            lines.append(f"- {title}: " + " → ".join(chain))
            for route in chain:
                what = at.get(route) or []
                if what:
                    lines.append(f"    {route}: " + "; ".join(
                        f"{w[:1].lower()}{w[1:]}" for w in what))
        else:
            first = steps[0]
            lines.append(f"- {title}: starts at {chain[0] if chain else entry.get('route') or '/'}"
                         f" — {first[:1].lower()}{first[1:]}")

    lines += [
        "",
        "Every journey above must be walkable end to end when you are done — "
        "start at the first route, do what each step says, arrive at the last. "
        "That is the test of a finished app, and a journey that stops at a "
        "page that does not exist yet, or at a button that does nothing, is a "
        "half-built app however good the pages before it look.",
        "",
        "Build the navigation from that map and nothing else. Every route in "
        "it exists as a real page; every link points at a route in it. A page "
        "that no other page links to is unreachable and does not count as "
        "built, and a link to a route you have not written is a dead link — "
        "if a page is not ready yet, do not link to it at all. After an action "
        "finishes — a form saved, an item added — send the person to the next "
        "page in the journey above, not back to a blank form.",
        "",
    ]
    return lines
