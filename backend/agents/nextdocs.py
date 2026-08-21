"""Next.js's own error pages, fetched for the fix prompt."""
import logging
import re
import threading
from pathlib import Path

import requests

log = logging.getLogger("nextdocs")


MESSAGE_LINK_RE = re.compile(
    r"nextjs\.org/docs/messages/([a-z0-9][a-z0-9-]{2,60})", re.I)

BASE = "https://nextjs.org/docs/messages"
TIMEOUT = 12


MAX_BYTES = 60_000


MAX_PAGES = 2

_lock = threading.Lock()
_mem: dict = {}


def cache_dir() -> Path:
    return Path.home() / ".agentforge" / "docs" / "messages"


def slugs_in(text: str) -> list:
    """Every error-page slug named in a blob of build or server output."""
    seen, out = set(), []
    for m in MESSAGE_LINK_RE.finditer(text or ""):
        s = m.group(1).lower()
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def fetch(slug: str, *, offline: bool = False) -> str:
    """The markdown for one error page, or ""."""
    slug = (slug or "").strip().lower()

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,60}", slug):
        return ""

    with _lock:
        if slug in _mem:
            return _mem[slug]

    fp = cache_dir() / f"{slug}.md"
    try:
        if fp.is_file():
            body = fp.read_text(encoding="utf-8")
            with _lock:
                _mem[slug] = body
            return body
    except Exception as e:
        log.debug(f"docs cache read failed for {slug}: {e}")

    if offline:
        return ""

    try:
        r = requests.get(f"{BASE}/{slug}.md", timeout=TIMEOUT,
                         headers={"Accept": "text/markdown"})
        if r.status_code != 200:
            log.debug(f"docs {slug}: HTTP {r.status_code}")
            return ""
        body = r.text[:MAX_BYTES]

        if (not body.strip()
                or "<!DOCTYPE" in body[:200]
                or body.lstrip().startswith("# Page Not Found")):
            log.debug(f"docs {slug}: no such page")
            return ""
    except Exception as e:
        log.debug(f"docs {slug} unreachable: {e}")
        return ""

    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding="utf-8")
    except Exception as e:
        log.debug(f"docs cache write failed for {slug}: {e}")

    with _lock:
        _mem[slug] = body
    return body


def guidance_for(text: str, *, offline: bool = False,
                 max_pages: int = MAX_PAGES) -> str:
    """The prompt section for whatever error pages `text` names, or ""."""
    parts = []
    for slug in slugs_in(text)[:max_pages]:
        body = fetch(slug, offline=offline)
        if body:
            parts.append(f"### {slug}\n{body.strip()}")
    if not parts:
        return ""
    return ("## How Next.js says to fix this\n"
            "These are Next.js's own error pages for the errors above. They "
            "give the canonical fix and the trade-offs. Follow them rather "
            "than guessing.\n\n" + "\n\n".join(parts))
