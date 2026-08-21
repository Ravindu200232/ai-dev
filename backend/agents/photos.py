"""Real photographs for the demo pages, inlined so they survive going offline."""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("agentforge.photos")

MARKER = re.compile(r"\{\{\s*photo\s*:\s*([^}]{2,120}?)\s*\}\}", re.I)

_TIMEOUT = 12
_MAX_BYTES = 900_000  # A demo with six 3MB photographs is not a demo


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read(_MAX_BYTES + 1)


def _unsplash(phrase: str, key: str) -> str:
    q = urllib.parse.quote(phrase)
    url = (f"https://api.unsplash.com/search/photos?query={q}"
           f"&per_page=1&orientation=landscape&content_filter=high")
    data = json.loads(_get(url, {"Authorization": f"Client-ID {key}",
                                 "Accept-Version": "v1"}))
    hits = data.get("results") or []
    if not hits:
        return ""
    urls = hits[0].get("urls") or {}
    return urls.get("small") or urls.get("regular") or ""


# Words that describe the mood of a photograph, not its subject.
_MOOD = frozenset("""
a an the of with in at on and or for by from into onto over under
minimal minimalist modern elegant luxury luxurious cozy cosy warm soft bright
airy clean sleek stylish beautiful stunning gorgeous premium boutique refined
professional simple calm serene peaceful quiet moody dramatic vibrant lush
natural rustic contemporary classic timeless chic trendy fresh crisp
light lighting sunlight morning evening golden hour dusk dawn daylight
closeup close-up overhead flatlay flat-lay wide shot photo photograph image
picture view scene background high-quality high quality realistic detailed
""".split())


def _queries(phrase: str) -> list[str]:
    """The searches to try for one phrase, most specific first, at most four."""
    words = [w for w in re.split(r"[\s,]+", phrase.strip()) if w]
    if not words:
        return []
    subject = [w for w in words if w.lower() not in _MOOD]
    tries = [" ".join(words)]
    if subject and len(subject) < len(words):
        tries.append(" ".join(subject))
    if len(subject) > 2:
        tries.append(" ".join(subject[-2:]))
    if len(subject) > 1:
        tries.append(subject[-1])
    out: list[str] = []
    for t in tries:
        if t and t not in out:
            out.append(t)
    return out[:4]


def _openverse(phrase: str, _key: str = "") -> str:
    """Creative-Commons search — no key, and it filters to commercial licences."""
    for attempt in _queries(phrase):
        q = urllib.parse.quote(attempt)
        url = (f"https://api.openverse.org/v1/images/?q={q}&page_size=3"
               f"&license_type=commercial&mature=false")
        data = json.loads(_get(url, {"User-Agent": "AgentForge/1.0 (dev tool)"}))
        for hit in data.get("results") or []:
            # Thumbnail is a resized copy Openverse serves itself.
            found = hit.get("thumbnail") or hit.get("url") or ""
            if found:
                return found
    return ""


def _pexels(phrase: str, key: str) -> str:
    q = urllib.parse.quote(phrase)
    url = (f"https://api.pexels.com/v1/search?query={q}"
           f"&per_page=1&orientation=landscape")
    data = json.loads(_get(url, {"Authorization": key}))
    hits = data.get("photos") or []
    if not hits:
        return ""
    src = hits[0].get("src") or {}
    return src.get("medium") or src.get("large") or ""


def _data_uri(url: str) -> str:
    raw = _get(url, {"User-Agent": "AgentForge/1.0"})
    if not raw or len(raw) > _MAX_BYTES:
        return ""
    kind = "jpeg"
    if raw[:8].startswith(b"\x89PNG"):
        kind = "png"
    elif raw[:4] == b"RIFF":
        kind = "webp"
    return f"data:image/{kind};base64," + base64.b64encode(raw).decode("ascii")


def _placeholder(phrase: str) -> str:
    """A drawn stand-in, as an SVG data URI, when no photograph can be had."""
    label = (phrase[:40] + "…") if len(phrase) > 40 else phrase
    label = label.replace("&", "&amp;").replace("<", "&lt;")
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='520'>"
        "<rect width='800' height='520' fill='#e7e3de'/>"
        "<text x='400' y='268' text-anchor='middle' font-size='22' "
        "font-family='system-ui,sans-serif' fill='#8a8279'>"
        f"{label}</text></svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(
        svg.encode("utf-8")).decode("ascii")


def fill(html: str, settings: dict | None = None,
         on_log=None) -> tuple[str, int]:
    """Replace every `{{photo: …}}` with an inlined image."""
    settings = settings or {}
    unsplash = str(settings.get("unsplash_key", "")).strip()
    pexels = str(settings.get("pexels_key", "")).strip()

    phrases = list(dict.fromkeys(m.group(1).strip()
                                 for m in MARKER.finditer(html or "")))
    if not phrases:
        return html, 0

    # Keyed sources first.
    sources = [("Unsplash", _unsplash, unsplash),
               ("Pexels", _pexels, pexels),
               ("Openverse", _openverse, "-")]

    cache: dict[str, str] = {}
    got = 0
    for phrase in phrases:
        uri = ""
        for name, fn, key in sources:
            if not key:
                continue
            try:
                found = fn(phrase, key)
                if found:
                    uri = _data_uri(found)
                    if uri:
                        got += 1
                        break
            except Exception as e:
                log.debug(f"{name} '{phrase}': {e}")
                if on_log:
                    on_log("DEBUG", f"{name} could not find '{phrase}': "
                                    f"{str(e)[:60]}")
        cache[phrase] = uri or _placeholder(phrase)

    filled = MARKER.sub(
        lambda m: cache.get(m.group(1).strip(), _placeholder(m.group(1))), html)

    if on_log:
        via = "Unsplash" if unsplash else "Pexels" if pexels else "Openverse (no key needed)"
        on_log("INFO", f"   🖼 {got} of {len(phrases)} photograph(s) found via "
                       f"{via} and inlined"
                       + ("" if got == len(phrases) else
                          f" — {len(phrases) - got} drawn as placeholders"))
    return filled, got


def configured(settings: dict | None = None) -> bool:
    settings = settings or {}
    return bool(str(settings.get("unsplash_key", "")).strip()
                or str(settings.get("pexels_key", "")).strip())
