"""Post-repair guards that keep an E2E fix inside the proven root cause."""
from __future__ import annotations

import re


_AUTH_PATTERNS = (
    re.compile(r"\bredirect\s*\(\s*['\"]/?login", re.I),
    re.compile(r"\b(?:router\.)?(?:push|replace)\s*\(\s*['\"]/?login", re.I),
    re.compile(r"\bauth\.api\.getSession\b", re.I),
    re.compile(r"\bgetSession\s*\(", re.I),
    re.compile(r"from\s+['\"]@/lib/auth['\"]", re.I),
)
_ATTR_RE = re.compile(r"\b(name|id|aria-label|data-testid)\s*=\s*['\"]([^'\"]+)['\"]", re.I)


def _words(text: str) -> set[str]:
    raw = set(re.findall(r"[a-z0-9]+", str(text or "").lower()))
    stop = {"field", "button", "role", "input", "selector", "page", "missing",
            "nothing", "matched", "expected", "test", "fix", "app", "the", "a",
            "an", "to", "of", "and", "or", "for", "with"}
    return {w for w in raw if len(w) > 2 and w not in stop}


def _selector_words(failure) -> set[str]:
    text = f"{getattr(failure, 'name', '')} {getattr(failure, 'message', '')}"
    m = re.search(r"(?:field\s+['\"]|field=)([A-Za-z0-9_-]+)", text, re.I)
    if m:
        return _words(m.group(1))
    m = re.search(r"(?:role\s+/|name=/)([^/]+)/", text, re.I)
    if m:
        return _words(m.group(1).replace("\\s", " "))
    m = re.search(r"testid\s+['\"]([^'\"]+)", text, re.I)
    return _words(m.group(1)) if m else set()


def _new_attributes(before: str, after: str) -> list[tuple[str, str]]:
    old = {(k.lower(), v.lower()) for k, v in _ATTR_RE.findall(before or "")}
    return [(k.lower(), v) for k, v in _ATTR_RE.findall(after or "")
            if (k.lower(), v.lower()) not in old]


def _witnessed_auth_symptom(failure) -> bool:
    """The failure itself showed an auth symptom — not merely a theory of one."""
    blob = " ".join(str(getattr(failure, k, "") or "")
                    for k in ("name", "message", "stack"))
    return bool(re.search(
        r"HTTP\s+40[13]\b|\bat\s+/login\b|DOM at /login\b|"
        r"redirected .* to /login|no authenticated browser session|"
        r"rejects a live session|get-session.*\bnull\b", blob, re.I))


_ASYNC_EXPORT_RE = re.compile(
    r"export\s+async\s+function\s+([A-Za-z_$][\w$]*)|"
    r"export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*async\b")

# `.then`/`.catch`/`.finally` are how you are SUPPOSED to use
_PROMISE_OK = {"then", "catch", "finally"}


def _async_helpers(files: dict[str, str]) -> set[str]:
    """Every project-local helper that returns a promise."""
    out = set()
    for rel, body in (files or {}).items():
        rel = str(rel or "")
        if not rel.startswith(("lib/", "app/api/", "components/")):
            continue
        for a, b in _ASYNC_EXPORT_RE.findall(str(body or "")):
            name = a or b
            if name:
                out.add(name)
    return out


def _promise_used_as_value(body: str, helpers: set[str]) -> list[str]:
    """`getCollection('x').find(...)` — the call returns a promise, not the value."""
    out = []
    for name in sorted(helpers):
        start = 0
        while True:
            at = body.find(name + "(", start)
            if at < 0:
                break
            start = at + len(name)
            before = body[at - 1] if at else " "
            if before.isalnum() or before in "._$":
                continue                       # `db.getCollection(` is not ours
            depth, i = 0, at + len(name)
            while i < len(body):
                ch = body[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if i >= len(body):
                break
            tail = body[i + 1:i + 40].lstrip()
            if not tail.startswith("."):
                continue
            member = re.match(r"\.\s*([A-Za-z_$][\w$]*)", tail)
            if not member or member.group(1) in _PROMISE_OK:
                continue
            out.append(f"{name}(...).{member.group(1)}")
    return list(dict.fromkeys(out))


def validate_repair_invariants(before_files: dict[str, str], after_files: dict[str, str],
                               changed: list[str], verdict: str, diagnosis: dict,
                               failure=None, journey=None) -> list[str]:
    """Return concrete reasons a patch escaped its evidence boundary."""
    verdict = str(verdict or "").upper()
    problems: list[str] = []
    # The hypothesis text used to count as auth evidence
    auth_evidence = _witnessed_auth_symptom(failure)
    selector_words = _selector_words(failure)
    helpers = _async_helpers(after_files)

    for rel in changed or []:
        before = str(before_files.get(rel, "") or "")
        after = str(after_files.get(rel, "") or "")
        if not after or before == after:
            continue

        if helpers:
            introduced = [m for m in _promise_used_as_value(after, helpers)
                          if m not in _promise_used_as_value(before, helpers)]
            if introduced:
                problems.append(
                    f"{rel}: {introduced[0]} reads a member off a promise -- "
                    f"the helper is async, so this throws at request time even "
                    f"though the build is clean. Await the CALL: "
                    f"`(await {introduced[0].split('(')[0]}(...)).<method>`")

        if verdict != "AUTH_FIX" and not auth_evidence:
            for pattern in _AUTH_PATTERNS:
                if len(pattern.findall(after)) > len(pattern.findall(before)):
                    problems.append(
                        f"{rel}: added authentication/login routing while the proven root cause was not auth")
                    break

        if selector_words:
            for attr, value in _new_attributes(before, after):
                if attr not in {"name", "id", "aria-label", "data-testid"}:
                    continue
                got = _words(value)
                if got and not (got & selector_words):
                    line = next((ln for ln in after.splitlines() if value in ln), "")
                    if re.search(r"<(?:input|select|textarea|button)\b", line, re.I):
                        problems.append(
                            f"{rel}: added {attr}={value!r}, which does not match the failing selector concept {sorted(selector_words)}")
                        break
    return list(dict.fromkeys(problems))


def repair_guard_feedback(violations: list[str], diagnosis: dict, failure=None) -> str:
    exact = str(getattr(failure, "name", "") or "")
    return (
        "\n\nREPAIR INVARIANT GUARD\n"
        "The previous candidate patch was rolled back because it changed behavior outside the proven root cause.\n"
        + "\n".join(f"- {v}" for v in violations[:6])
        + f"\nKeep auth/role/route policy unchanged unless the evidence explicitly proves an auth defect.\n"
        + (f"Preserve the exact failing scenario intent: {exact}\n" if exact else "")
        + "Repair the smallest production owner that satisfies the accepted workflow; do not rename a different field to make the test pass.\n"
    )
