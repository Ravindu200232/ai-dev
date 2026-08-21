"""Point at something in the preview and change just that."""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field

from .exports import parse_exports, resolve_local

log = logging.getLogger("picker")

MAX_CANDIDATES = 12

DECISIVE_MARGIN = 1.3

LOCAL_IMPORT_RE = re.compile(
    r"""(?:from|import)\s*['"]((?:\.{1,2}/|@/)[^'"]+)['"]""")


@dataclass
class Resolution:
    path: str = ""
    line: int = 0
    score: int = 0
    candidates: list = field(default_factory=list)
    used_model: bool = False
    reason: str = ""


class ElementResolver:
    def __init__(self, arch, analyzer):
        self.arch = arch
        self.az = analyzer

    def route_closure(self, route: str) -> list:
        """The page for `route`, every layout above it, and everything they import."""
        files = self.az.code_files()
        routes = self.az.enumerate_routes()
        path = (route or "/").split("?")[0].rstrip("/") or "/"

        entry = None
        for url, r in routes.items():
            if r["kind"] != "page":
                continue
            if url == path or self.az._route_matches(path, [url]):
                entry = r["file"]
                break
        seeds = [entry] if entry else []

        segs = [s for s in path.strip("/").split("/") if s]
        for i in range(len(segs), -1, -1):
            for ext in (".js", ".jsx"):
                cand = "/".join(["app"] + segs[:i] + ["layout" + ext])
                if cand in files:
                    seeds.append(cand)

        seen, queue = set(), [s for s in seeds if s]
        while queue:
            rel = queue.pop()
            if rel in seen or rel not in files:
                continue
            seen.add(rel)
            for spec in LOCAL_IMPORT_RE.findall(files[rel]):
                target = resolve_local(rel, spec, files)
                if target and target not in seen:
                    queue.append(target)
        return sorted(seen)

    def score_candidates(self, el: dict, pool: list) -> list:
        files = self.az.code_files()
        pool = [p for p in pool if p in files] or list(files)
        text = (el.get("text") or "").strip()
        cls = (el.get("className") or "").strip()
        attrs = el.get("attrs") or {}
        el_id = (el.get("id") or "").strip()
        testid = attrs.get("data-testid") or ""
        tag = (el.get("tag") or "").lower()

        out = []
        for rel in pool:
            body = files[rel]
            score, lines = 0, []

            def hit(needle, weight):
                nonlocal score
                if not needle or len(str(needle)) < 2 or needle not in body:
                    return
                score += weight
                lines.append(body[:body.index(needle)].count("\n") + 1)

            hit(el_id, 6)
            hit(testid, 6)
            if text and len(text) >= 3:
                hit(text[:80], 5)
            if cls and len(cls) >= 6:
                hit(cls, 4)
            for key in ("placeholder", "alt", "aria-label", "href", "name"):
                hit(attrs.get(key), 4)
            if tag and re.search(rf"<{re.escape(tag)}[\s/>]", body):
                score += 1
            anc = 0
            for a in (el.get("chain") or [])[:6]:
                t = (a.get("text") or "").strip()
                if t and len(t) >= 4 and t[:60] in body and anc < 3:
                    score += 1
                    anc += 1
            if score:
                out.append((rel, score, sorted(set(lines))))
        out.sort(key=lambda c: (-c[1], c[0]))
        return out[:MAX_CANDIDATES]

    def disambiguate(self, el: dict, ranked: list) -> tuple:
        files = self.az.code_files()
        blocks = []
        for rel, score, lines in ranked[:6]:
            body = files.get(rel, "").splitlines()
            excerpt = []
            for ln in lines[:3]:
                lo, hi = max(0, ln - 3), min(len(body), ln + 3)
                excerpt.append(f"  line {ln}:\n" +
                               "\n".join(f"    {body[i]}" for i in range(lo, hi)))
            blocks.append(f"--- {rel} (score {score}) ---\n" + "\n".join(excerpt))

        system = ("Pick which source file renders the element the user clicked.\n"
                  "Reply with exactly one line and nothing else:\n"
                  "PICK <path> <line>")
        user = (f"Route: {el.get('route', '/')}\n"
                f"Element: <{el.get('tag')}"
                + (f" id=\"{el.get('id')}\"" if el.get("id") else "")
                + (f" class=\"{el.get('className')}\"" if el.get("className") else "")
                + f">{(el.get('text') or '')[:120]}</{el.get('tag')}>\n\n"
                + "\n\n".join(blocks))
        buf = []
        try:
            self.arch._stream([{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                              buf.append, temperature=0.1)
        except Exception as e:
            log.warning(f"disambiguation failed: {e}")
            return ranked[0][0], (ranked[0][2] or [0])[0]
        m = re.search(r"PICK\s+(\S+)\s*(\d+)?", "".join(buf))
        if m and m.group(1).strip("`") in files:
            return m.group(1).strip("`"), int(m.group(2) or 0)
        return ranked[0][0], (ranked[0][2] or [0])[0]

    def resolve(self, el: dict) -> Resolution:
        closure = self.route_closure(el.get("route", "/"))
        ranked = self.score_candidates(el, closure)
        if not ranked:

            ranked = self.score_candidates(el, list(self.az.code_files()))
        if not ranked:
            return Resolution(reason="nothing in the project matches this element")

        top = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0
        decisive = top[1] > 0 and (runner == 0 or top[1] >= runner * DECISIVE_MARGIN)
        cands = [{"path": p, "score": s} for p, s, _ in ranked]
        if decisive:
            return Resolution(path=top[0], line=(top[2] or [0])[0], score=top[1],
                              candidates=cands, used_model=False,
                              reason="unambiguous")
        path, line = self.disambiguate(el, ranked)
        return Resolution(path=path, line=line,
                          score=next((s for p, s, _ in ranked if p == path), 0),
                          candidates=cands, used_model=True,
                          reason="scores were close; the model chose")


def render_index(files: dict) -> dict:
    """`{path: {paths that import it}}` — the edge every other helper lacks."""
    out = {}
    for rel, body in (files or {}).items():
        if not rel.endswith((".js", ".jsx")):
            continue
        for spec in LOCAL_IMPORT_RE.findall(body or ""):
            target = resolve_local(rel, spec, files)
            if target:
                out.setdefault(target, set()).add(rel)
    return out


def _route_of_page(rel: str) -> str:
    """`app/(auth)/login/page.jsx` -> `/login`."""
    parts = rel.split("/")[1:-1]
    segs = [p for p in parts if not (p.startswith("(") and p.endswith(")"))]
    return "/" + "/".join(segs) if segs else "/"


def routes_rendering(files: dict, target: str) -> list:
    """Every route that puts `target` on the screen."""
    files = files or {}
    target = (target or "").lstrip("./").replace("\\", "/")
    if not target:
        return []

    pages = [r for r in files if re.fullmatch(r"app/.*page\.jsx?", r)]
    index = render_index(files)

    seen, queue, roots = set(), [target], set()
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        name = rel.rsplit("/", 1)[-1]
        if re.fullmatch(r"(page|layout)\.jsx?", name):
            roots.add(rel)

            continue
        for importer in index.get(rel, ()):
            if importer not in seen:
                queue.append(importer)

    out = set()
    for rel in roots:
        name = rel.rsplit("/", 1)[-1]
        if name.startswith("page."):
            out.add(_route_of_page(rel))
            continue

        under = rel.rsplit("/", 1)[0] + "/"
        for p in pages:
            if p.startswith(under):
                out.add(_route_of_page(p))
    return sorted(out)


def guard_scope(old: str, new: str, *, anchor: str = "", removing: bool = False,
                adding: bool = False, retexting: bool = False,
                designing: bool = False,
                max_changed_frac: float = 0.20, min_abs: int = 25) -> str | None:
    """Reject a rewrite that did more than the instruction asked for."""
    if not new or not new.strip():
        return "the rewrite is empty"
    floor = 0.25 if designing else 0.6
    if len(new) < floor * len(old):
        return (f"the rewrite is {len(new)} characters against {len(old)} before "
                f"— the file was truncated rather than edited")
    if designing:
        return None

    old_lines, new_lines = old.splitlines(), new.splitlines()
    changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, old_lines, new_lines).get_opcodes():
        if tag != "equal":
            changed += max(i2 - i1, j2 - j1)
    ceiling = max(min_abs, int(len(old_lines) * max_changed_frac))
    if changed > ceiling and not adding:
        return (f"{changed} lines changed out of {len(old_lines)}; this edit "
                f"should touch at most {ceiling}. Rewrite the file again, "
                f"identical to the original except for the one element")

    a, b = parse_exports(old), parse_exports(new)
    lost = a.named - b.named
    if lost:
        return (f"the rewrite dropped {', '.join(sorted(lost))} from the file's "
                f"exports — other files import those")
    if a.has_default and not b.has_default:
        return "the rewrite dropped the default export"

    if (anchor and not removing and not retexting and len(anchor) >= 4
            and anchor not in new):
        return (f"the element is gone: {anchor[:60]!r} no longer appears in the "
                f"file, but the instruction was not to remove it")
    return None


REMOVAL_WORDS = ("remove", "delete", "hide", "get rid", "take out", "drop",
                 "ain karanna", "ayin karanna", "නැති කරන්න", "ඉවත් කරන්න")


GLOBAL_WORDS = ("everywhere", "all pages", "every page", "site-wide",
                "sitewide", "whole site", "across the site", "globally",
                "all routes", "hama thanama", "hama page ekakama",
                "siyaluma", "හැම තැනම", "හැම පිටුවකම", "සියලුම")


def looks_like_global(instruction: str) -> bool:
    """Whether the user said, in so many words, that they mean every page."""
    low = (instruction or "").lower()
    return any(w in low for w in GLOBAL_WORDS)


PAGE_ONLY_WORDS = ("only", "just this", "just on", "this page", "here only",
                   "on this route", "witharai", "vitharai", "විතරයි",
                   "මේ පිටුවේ", "me page eke")


def looks_like_page_only(instruction: str) -> bool:
    """Whether the user has said they mean this one route."""
    low = (instruction or "").lower()
    if looks_like_global(low):
        return False
    return any(w in low for w in PAGE_ONLY_WORDS)


ADDITION_WORDS = ("add", "insert", "append", "put a", "put an", "create",
                  "new section", "another", "extra", "include a", "include an",
                  "danna", "add karanna", "aluth", "අලුත්", "එකතු කරන්න")


def looks_like_removal(instruction: str) -> bool:
    low = (instruction or "").lower()
    return any(w in low for w in REMOVAL_WORDS)


def looks_like_addition(instruction: str) -> bool:
    low = (instruction or "").lower()
    if looks_like_removal(low):
        return False
    return any(w in low for w in ADDITION_WORDS)


RETEXT_RE = re.compile(
    r"\b(?:rename|retitle|reword|relabel)\b"
    r"|\bchange\b[^.]{0,60}\b(?:to|into)\b"
    r"|\breplace\b[^.]{0,60}\bwith\b"
    r"|\b(?:text|wording|label|caption|title|heading|copy)\b[^.]{0,40}"
    r"\b(?:to|say|reads?|instead)\b"
    r"|\bmake it (?:say|read)\b|\bcall it\b|\bset it to\b"
    r"|\bwenas karanna\b|වෙනස් කරන්න",
    re.I)


def looks_like_retext(instruction: str) -> bool:
    """Whether the instruction is asking for the element's words to change."""
    return bool(RETEXT_RE.search(instruction or ""))


ELEMENT_EDIT_SYSTEM = """\
You are editing ONE part of a Next.js 16 App Router file — changing the element
the user clicked, or adding a new element or section right where they pointed.

You are given the element the user clicked, the route it is on, and the COMPLETE
current source of the file that appears to render it. Before changing code,
establish which current source actually owns the requested behavior. If a child,
caller, API, server action or shared component is uncertain, use the read-only
workspace tools and inspect it instead of guessing from a filename.

THE SCOPE RULE: CHANGE EVERYTHING THE SELECTED REGION NEEDS, AND NOTHING UNRELATED.

  • Output the COMPLETE file. Preserve unrelated sections and public contracts,
    but imports/helpers used by the selected region may change when required.
  • Do NOT perform unrelated cleanup, reformatting or renames. Do NOT rename or
    remove an export that other files depend on.
  • A large redesign of the selected section is valid even when it changes many
    lines. Size is never a reason to refuse the requested edit.

DO EVERYTHING THEY ASKED FOR. Rewrite the text, change the animation, redesign
the whole thing — whatever the request is, that part of the file is yours to
change completely. There is no such thing as too big a change to what they
pointed at.

THAT INCLUDES REMOVING THINGS. "Take this section out", "drop the badges",
"get rid of the icons" — do exactly that, and delete the markup properly rather
than hiding it behind a class. Removal is a normal request, not a special case.

WHAT MUST NOT HAPPEN is something disappearing that they never mentioned. The
tools row is still there after "make the heading bigger". The FAQ is still
there after "restyle the cards". Ask yourself one question before you answer:
is anything gone that they did not ask you to remove? If so, put it back.

If you cannot find the thing they described, change nothing and say so. Do not
tidy the area instead and do not invent a replacement — an empty answer is
right, a plausible substitute is not.
  • Respect the Server/Client boundary. If what you are adding needs a hook or
    event handler and this file is a Server Component, the correct solution may
    require a small 'use client' component. If that component is not already in
    this file, reply NEED <path> so AgentForge can automatically expand the edit.
  • Match what is already there: the same palette, spacing scale, radius,
    border treatment and heading sizes the surrounding markup uses. A new
    section that does not look like its neighbours is a wrong answer.

IF YOU NEED A PACKAGE THAT IS NOT INSTALLED, ASK FOR IT FIRST. Emit the command
on its own, BEFORE the file that imports it:

<run_command>npm install embla-carousel-react</run_command>

  • One package per command, real npm names only, no flags beyond `npm install`.
  • Already installed, so never ask for these: react, react-dom, next, mongodb,
    tailwindcss, lucide-react, framer-motion, better-auth.
  • Prefer what is already there. Reaching for a new dependency to do something
    Tailwind and framer-motion already do is not an improvement.

If source inspection proves the complete change cannot be made in this file,
reply with exactly NEED <path> and nothing else. Do not emit NEED merely because
a dependency might matter — inspect it first with the workspace tools.

Emit exactly one <write_file path="…"> block.
"""


def describe(el: dict) -> str:
    """A compact, human-readable rendering of the clicked element."""
    attrs = el.get("attrs") or {}
    bits = [f"<{el.get('tag', 'div')}"]
    if el.get("id"):
        bits.append(f' id="{el["id"]}"')
    if el.get("className"):
        bits.append(f' class="{el["className"][:120]}"')
    for k in ("href", "type", "placeholder", "aria-label"):
        if attrs.get(k):
            bits.append(f' {k}="{attrs[k]}"')
    bits.append(">")
    text = (el.get("text") or "").strip()
    if text:
        bits.append(text[:160])
        bits.append(f"</{el.get('tag', 'div')}>")
    chain = " › ".join(a.get("tag", "") for a in (el.get("chain") or [])[:4])
    out = "".join(bits)
    if chain:
        out += f"\nInside: {chain}"
    return out


JSX_TEXT_RE = re.compile(r">\s*([^<>{}\n][^<>{}]{2,80}?)\s*<")


ARRAY_STR_RE = re.compile(r"\[[^\]]*?\]")
QUOTED_RE = re.compile(r"""['"]([^'"\n]{3,60})['"]""")


def visible_strings(src: str) -> set:
    """The words a page shows, normalised."""
    out = set()
    for m in JSX_TEXT_RE.finditer(src or ""):
        t = " ".join(m.group(1).split())
        if t and not t.startswith(("/", "*")) and any(c.isalpha() for c in t):
            out.add(t)
    for arr in ARRAY_STR_RE.findall(src or ""):
        for t in QUOTED_RE.findall(arr):
            t = " ".join(t.split())

            if t and not re.search(r"\b(?:bg|text|border|flex|grid|p|m)-", t):
                out.add(t)
    return out


def lost_content(old: str, new: str, tolerance: float = 0.2) -> str | None:
    """What the rewrite dropped from the page, or None."""
    before = visible_strings(old)
    if len(before) < 4:
        return None
    gone = sorted(t for t in before if t not in (new or ""))
    if len(gone) <= max(1, int(len(before) * tolerance)):
        return None
    sample = ", ".join(repr(t) for t in gone[:5])
    return (f"the rewrite dropped {len(gone)} of {len(before)} things the page "
            f"showed — {sample}. Put them back: this edit was meant to change "
            f"the layout, not remove content")
