"""The Next.js documentation that ships inside the installed package."""
import logging
import re
from pathlib import Path

log = logging.getLogger("docsindex")


TOPICS = {
    "use-client": (
        "01-app/03-api-reference/01-directives/use-client.md",
        "the 'use client' directive — what a Client Component may and may not do"),
    "use-server": (
        "01-app/03-api-reference/01-directives/use-server.md",
        "the 'use server' directive and Server Actions"),
    "server-and-client": (
        "01-app/01-getting-started/05-server-and-client-components.md",
        "the server/client boundary, and how to split a page across it"),
    "fetching-data": (
        "01-app/01-getting-started/06-fetching-data.md",
        "reading data in a Server Component vs a Client Component"),
    "updating-data": (
        "01-app/01-getting-started/07-mutating-data.md",
        "writing data — forms, actions and route handlers"),
    "error-handling": (
        "01-app/01-getting-started/10-error-handling.md",
        "error.js, not-found.js and what a thrown error does to a route"),
    "page": (
        "01-app/03-api-reference/03-file-conventions/page.md",
        "page.js — its props, and that params/searchParams are Promises"),
    "layout": (
        "01-app/03-api-reference/03-file-conventions/layout.md",
        "layout.js — the root layout's rules and what belongs in it"),
    "route": (
        "01-app/03-api-reference/03-file-conventions/route.md",
        "route.js — named GET/POST exports, and the Response shape"),
    "cookies": (
        "01-app/03-api-reference/04-functions/cookies.md",
        "cookies() — where it may be called, and why it is async"),
    "redirect": (
        "01-app/03-api-reference/04-functions/redirect.md",
        "redirect() — server-side only, and how to navigate from a client file"),
    "dynamic": (
        "01-app/03-api-reference/03-file-conventions/02-route-segment-config/index.md",
        "the route segment config exports, incl. `dynamic`"),
}


def docs_root(project_dir: Path) -> Path | None:
    """The installed docs directory, or None on Next < 16.2 / no install."""
    root = Path(project_dir) / "node_modules" / "next" / "dist" / "docs"
    return root if root.is_dir() else None


def available(project_dir: Path) -> dict:
    """`{topic: description}` for the topics whose file is really present."""
    root = docs_root(project_dir)
    if not root:
        return {}
    return {t: desc for t, (rel, desc) in TOPICS.items()
            if (root / rel).is_file()}


def index_block(project_dir: Path) -> str:
    """The topic table for the system prompt, or "" when there are no docs."""
    got = available(project_dir)
    if not got:
        return ""
    lines = "\n".join(f"      {t:20} {d}" for t, d in got.items())
    return (
        "    You can read the documentation for the EXACT Next.js version "
        "installed here.\n"
        "    Do it whenever you are unsure — it is cheaper than a wrong file:\n\n"
        "      <read_docs topic=\"use-client\"/>\n\n"
        "    Topics:\n" + lines + "\n\n"
        "    One tag per line, at most 3 per reply. The page comes back in the "
        "next message.\n"
        "    This is version-matched: prefer it over what you remember, which "
        "is probably Next 13-15.\n")


READ_RE = re.compile(r"<read_docs\s+topic=[\"']([\w-]+)[\"']\s*/?>")

MAX_PER_TURN = 3
MAX_BYTES = 24_000


def read(project_dir: Path, topic: str) -> str:
    """One documentation page, or a short refusal the model can act on."""
    topic = (topic or "").strip().lower()
    entry = TOPICS.get(topic)
    if not entry:
        return (f"[no such topic: {topic}. Available: "
                f"{', '.join(sorted(available(project_dir))) or 'none'}]")
    root = docs_root(project_dir)
    if not root:
        return "[the installed Next.js does not bundle documentation]"

    fp = root / entry[0]
    if not fp.is_file():
        return f"[{topic} is not in this version's documentation]"
    try:
        return fp.read_text(encoding="utf-8", errors="replace")[:MAX_BYTES]
    except Exception as e:
        log.debug(f"read_docs {topic}: {e}")
        return f"[could not read {topic}]"


def serve(project_dir: Path, reply: str) -> str:
    """The answer turn for whatever `<read_docs>` tags a reply contains, or ""."""
    topics, seen = [], set()
    for t in READ_RE.findall(reply or ""):
        t = t.lower()
        if t not in seen:
            seen.add(t)
            topics.append(t)
    if not topics:
        return ""
    parts = [f"--- Next.js docs: {t} ---\n{read(project_dir, t)}"
             for t in topics[:MAX_PER_TURN]]
    return "\n\n".join(parts)
