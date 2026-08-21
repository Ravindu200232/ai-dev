"""Stable ids for the controls a workflow depends on."""
import re

__all__ = ["action_testid", "workflow_actions", "actions_for_file",
           "actions_by_route", "ACTION_ATTR"]

ACTION_ATTR = "data-testid"


# The middle segment of a plan step is the action
_CONTROL_RE = re.compile(
    r"\b(click|press|tap|submit|save|apply|confirm|send|post|add|create|"
    r"book|reserve|buy|pay|confirm|update|change|edit|select|choose|pick|"
    r"cancel|delete|remove|approve|reject|mark|toggle|upload|search|filter|"
    r"sign in|log in|sign up|register)\b", re.I)

_QUOTED_RE = re.compile(r"['\"“”‘’]([^'\"“”‘’]{2,40})['\"“”‘’]")

_STOP = {"a", "an", "the", "it", "then", "and", "to", "on", "in", "of", "for",
         "with", "that", "this", "its", "their", "into", "from", "at", "by",
         "button", "link", "page", "form", "field", "input", "control"}

_SEP_RE = re.compile(r"\s+[—–-]{1,2}\s+")


def _slug(text: str, limit: int = 4) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", str(text or "").lower())
             if w and w not in _STOP]
    return "-".join(words[:limit])


def _route_slug(route: str) -> str:
    """`/item/[id]` -> `item`, `/` -> `home`, `/admin/orders` -> `orders`."""
    parts = [p for p in str(route or "").split("/")
             if p and not p.startswith("[")]
    if not parts:
        return "home"
    return _slug(parts[-1], limit=2) or "home"


def action_testid(route: str, phrase: str) -> str:
    """The id both the app and the test will use for one workflow control."""
    quoted = _QUOTED_RE.search(str(phrase or ""))
    label = quoted.group(1) if quoted else phrase
    body = _slug(label)
    if not body:
        body = _slug(phrase) or "action"
    out = f"{_route_slug(route)}-{body}".strip("-")
    out = re.sub(r"-{2,}", "-", out)
    return out[:48]


def _split_step(step: str):
    """(route, action phrase) for one plan step, or (None, None)."""
    text = str(step or "").strip()
    if not text:
        return None, None
    parts = [p.strip() for p in _SEP_RE.split(text) if p.strip()]
    if len(parts) < 2:
        return None, None
    route = parts[0]
    if not route.startswith("/"):
        return None, None
    phrase = parts[1]
    if not _CONTROL_RE.search(phrase):
        return None, None
    return route.split("?", 1)[0], phrase


def _page_route(path: str) -> str:
    """Convert one planned App Router page path to its route."""
    rel = str(path or "").replace("\\", "/").lstrip("./")
    m = re.match(r"^app/(.*/)?page\.(?:jsx|js)$", rel)
    if not m:
        return ""
    seg = re.sub(r"\((?:[^)]*)\)/?", "", m.group(1) or "").strip("/")
    return "/" + seg if seg else "/"


def _explicit_testid(plan: dict, route: str, phrase: str) -> str:
    """Prefer the id the planner explicitly attached to the route action."""
    want = {w for w in re.split(r"[^a-z0-9]+", str(phrase).lower())
            if w and w not in _STOP}
    quoted = _QUOTED_RE.search(str(phrase or ""))
    if quoted:
        want |= {w for w in re.split(r"[^a-z0-9]+", quoted.group(1).lower())
                 if w and w not in _STOP}
    best = (0, "")
    for phase in (plan or {}).get("phases") or []:
        for f in (phase or {}).get("files") or []:
            if not isinstance(f, dict) or _page_route(f.get("path")) != route:
                continue
            for action in f.get("actions") or []:
                text = str(action or "")
                m = re.search(r"\btestid=([a-z0-9][a-z0-9-]{0,47})\b", text, re.I)
                if not m:
                    continue
                words = {w for w in re.split(r"[^a-z0-9]+", text.lower())
                         if w and w not in _STOP and w != "testid"}
                score = len(want & words)
                if score > best[0]:
                    best = (score, m.group(1).lower())
    return best[1] if best[0] else ""


def workflow_actions(plan: dict) -> list:
    """Every control a workflow needs, as [{testid, route, phrase,...}]."""
    out, seen = [], {}
    for wf in (plan or {}).get("workflows") or []:
        if not isinstance(wf, dict):
            continue
        name = str(wf.get("name") or "workflow").strip()
        who = str(wf.get("who") or "").strip()
        for i, step in enumerate(wf.get("steps") or []):
            route, phrase = _split_step(step)
            if not route:
                continue
            tid = _explicit_testid(plan, route, phrase) or action_testid(route, phrase)
            # Two workflows pressing the same control share its id
            key = (route, phrase.lower())
            if key in seen:
                continue
            clash = [row for row in out if row["testid"] == tid
                     and (row["route"], row["phrase"].lower()) != key]
            if clash:
                tid = f"{tid}-{len(clash) + 1}"[:48]
            seen[key] = tid
            out.append({"testid": tid, "route": route, "phrase": phrase,
                        "workflow": name, "who": who, "step": i + 1})
    return out


def actions_by_route(plan: dict) -> dict:
    """{route: [action, …]} — the shape the E2E author and analyzer want."""
    grouped = {}
    for row in workflow_actions(plan):
        grouped.setdefault(row["route"], []).append(row)
    return grouped


def actions_for_file(plan: dict, path: str, routes: dict) -> list:
    """The actions whose route is rendered by `path`."""
    path = str(path or "").replace("\\", "/").lstrip("./")
    if not path:
        return []
    owned = {r for r, meta in (routes or {}).items()
             if str((meta or {}).get("file") or "").replace("\\", "/") == path}
    if not owned:
        return []
    return [row for row in workflow_actions(plan) if row["route"] in owned]
