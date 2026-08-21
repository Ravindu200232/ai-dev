"""The nouns THIS app is about, read off the idea and the plan.

Prompts used to teach every rule with one industry's nouns. A model building
something else then reads those nouns twenty times and drifts toward them.
Nothing here knows any domain — it takes the words the request and the plan
already use, and falls back to neutral ones when the app has not said yet.
"""
import re
from dataclasses import dataclass

# Words that are never the thing an app is about.
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "app",
    "apps", "web", "website", "site", "page", "pages", "system", "systems",
    "platform", "portal", "dashboard", "management", "manage", "managing",
    "admin", "administrator", "user", "users", "account", "accounts",
    "login", "signin", "signup", "auth", "role", "roles", "permission",
    "permissions", "data", "database", "collection", "collections", "table",
    "tables", "record", "records", "list", "lists", "view", "views", "form",
    "forms", "button", "buttons", "field", "fields", "feature", "features",
    "build", "building", "create", "creating", "make", "making", "add",
    "edit", "delete", "update", "search", "filter", "sort", "simple", "full",
    "small", "large", "modern", "clean", "nice", "good", "best", "real",
    "next", "react", "mongo", "mongodb", "javascript", "tailwind", "stack",
    "should", "would", "could", "need", "needs", "want", "wants", "have",
    "has", "can", "must", "where", "which", "when", "there", "their", "them",
    "they", "each", "every", "some", "any", "all", "one", "two", "also",
    "about", "over", "under", "than", "then", "who", "what", "how", "why",
    "audit", "logs", "notification", "notifications", "setting", "settings",
    "profile", "profiles", "session", "sessions", "token", "password",
    "online", "based", "free", "custom", "smart", "digital", "simple",
    "little", "basic", "quick", "easy", "responsive", "beautiful", "premium",
}

# Collections every app grows regardless of what it sells.
_INFRA = {"users", "accounts", "roles", "permissions", "sessions",
          "audit_logs", "auditlogs", "notifications", "settings", "files",
          "verification", "verifications", "account", "session", "user"}

_PLURAL_RULES = (("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("sses", "ss"),
                 ("xes", "x"), ("zes", "z"))


def singular(word: str) -> str:
    w = str(word or "").lower().strip()
    if len(w) < 4 or w.endswith(("ss", "us", "is", "as", "os")):
        return w
    for end, repl in _PLURAL_RULES:
        if w.endswith(end) and len(w) > len(end) + 1:
            return w[:-len(end)] + repl
    return w[:-1] if w.endswith("s") else w


def plural(word: str) -> str:
    w = str(word or "").lower().strip()
    if not w:
        return w
    if w.endswith(("ss", "ch", "sh", "x", "z")):
        return w + "es"              # class -> classes, box -> boxes
    if w.endswith("s"):
        return w                     # already plural
    if w.endswith("y") and w[-2:-1] not in "aeiou":
        return w[:-1] + "ies"
    return w + "s"


@dataclass
class Vocab:
    """Example nouns for one app. Neutral when the app has not said."""
    thing: str = "item"
    things: str = "items"
    child: str = "order"
    children: str = "orders"
    actor: str = "customer"

    def as_map(self) -> dict:
        return {
            "thing": self.thing, "things": self.things,
            "child": self.child, "children": self.children,
            "actor": self.actor,
            "Thing": self.thing.capitalize(),
            "Things": self.things.capitalize(),
            "Child": self.child.capitalize(),
            "Children": self.children.capitalize(),
        }


def _plan_collections(plan: dict) -> list:
    """Collection names the plan already committed to, business ones first."""
    out = []
    for key in ("collections", "data_model", "entities", "models"):
        block = (plan or {}).get(key)
        if isinstance(block, dict):
            out.extend(str(k) for k in block)
        elif isinstance(block, list):
            for row in block:
                if isinstance(row, str):
                    out.append(row)
                elif isinstance(row, dict):
                    name = row.get("name") or row.get("collection") or row.get("table")
                    if name:
                        out.append(str(name))
    for task in ((plan or {}).get("phases") or []) + ((plan or {}).get("tasks") or []):
        if isinstance(task, dict):
            out.extend(str(x) for x in (task.get("reads") or []))
            out.extend(str(x) for x in (task.get("writes") or []))
    clean, seen = [], set()
    for name in out:
        low = re.sub(r"[^a-z0-9_]", "", name.lower())
        if not low or low in seen or low in _INFRA:
            continue
        seen.add(low)
        clean.append(low)
    return clean


def _idea_nouns(text: str, skip: str = "") -> list:
    """The things the request is about, the ones it pluralises first.

    A description names its entities in the plural almost every time —
    "readers borrow copies from branches" — and its adjectives never are,
    which sorts the nouns out without knowing a single domain.
    """
    words = re.findall(r"[A-Za-z][A-Za-z-]{3,}", str(text or ""))
    counts, plurals, first = {}, {}, {}
    for i, raw in enumerate(words):
        low = raw.lower().replace("-", "")
        w = singular(low)
        if len(w) < 4 or w in _STOP or w in _INFRA or w == skip:
            continue
        counts[w] = counts.get(w, 0) + 1
        if low != w:
            plurals[w] = plurals.get(w, 0) + 1
        first.setdefault(w, i)
    return sorted(counts, key=lambda w: (-plurals.get(w, 0), -counts[w],
                                         first.get(w, 999)))


def _actor_of(plan: dict, idea: str) -> str:
    role = str((plan or {}).get("signup_role") or "").strip().lower()
    if role and role not in {"user", "admin", "administrator"}:
        return re.sub(r"[^a-z]", "", role) or "customer"
    for account in ((plan or {}).get("demo_accounts") or []):
        got = str((account or {}).get("role") or "").strip().lower()
        if got and got not in {"admin", "administrator", "user", "owner"}:
            return re.sub(r"[^a-z]", "", got) or "customer"
    for word in ("customer", "client", "member", "visitor", "buyer",
                 "subscriber", "student", "patient", "guest", "passenger"):
        if re.search(r"\b" + word + r"s?\b", str(idea or ""), re.I):
            return word
    return "customer"


def derive(idea: str = "", plan: dict = None) -> Vocab:
    """This app's example nouns, or neutral ones when it has not said."""
    plan = plan if isinstance(plan, dict) else {}
    v = Vocab()
    v.actor = _actor_of(plan, idea)
    names = _plan_collections(plan) or _idea_nouns(idea, skip=v.actor)
    picked = [n for n in names if n][:2]
    if picked:
        v.thing, v.things = singular(picked[0]), plural(singular(picked[0]))
    if len(picked) > 1:
        v.child, v.children = singular(picked[1]), plural(singular(picked[1]))
    elif picked:
        v.child, v.children = "order", "orders"
    if v.child == v.thing:
        v.child, v.children = "order", "orders"
    return v


# `<<thing>>` rather than `{thing}`: the prompts are full of JSX, and a
# `{children}` slot silently ate React's own children prop.
_SLOT_RE = re.compile(r"<<(thing|things|child|children|actor|"
                      r"Thing|Things|Child|Children)>>")


def render(text: str, vocab: Vocab) -> str:
    """Fill the vocabulary slots in a prompt, leaving every other brace."""
    table = vocab.as_map()
    return _SLOT_RE.sub(lambda m: table.get(m.group(1), m.group(0)), str(text or ""))
