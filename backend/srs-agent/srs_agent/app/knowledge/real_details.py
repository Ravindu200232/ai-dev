"""The customer's real details, read out of one box they paste into.

Asking for a name, a website, an email, a phone number, an address and four
social links is eight questions, and the interview has room for one. People
already keep these together — an email signature, an "about us" block, a
LinkedIn header — so the question is one box and the reading is done here.

Nothing is invented. A field that is not in the text does not appear, and
the whole thing is optional: an internal tool has no public identity and is
not asked to make one up.
"""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}\b")

# Long enough to be a real number, loose enough for any country's spacing.
PHONE_RE = re.compile(
    r"(?<![\w.])(\+?\d[\d\s().-]{7,20}\d)(?![\w.])")

URL_RE = re.compile(
    r"\b((?:https?://|www\.)[^\s,;<>\"')\]]+|[A-Za-z0-9][\w-]*(?:\.[\w-]+)+"
    r"/[^\s,;<>\"')\]]*)", re.I)
# `kamalperera.dev` is a website; `e.g` and `Node.js` are not. A real TLD is
# what tells them apart without a DNS lookup.
TLDS = (r"com|net|org|io|dev|app|co|ai|me|xyz|site|online|store|shop|tech|"
        r"design|studio|agency|digital|cloud|info|biz|edu|gov|blog|page|link|"
        r"[a-z]{2}")
BARE_DOMAIN_RE = re.compile(
    r"\b([A-Za-z0-9][\w-]{1,62}(?:\.[A-Za-z0-9-]{1,63})*\.(?:" + TLDS + r"))\b",
    re.I)

# Which link is which, by the host it lives on.
LINK_HOSTS = (
    ("linkedin", ("linkedin.com", "lnkd.in")),
    ("github", ("github.com", "gitlab.com", "bitbucket.org")),
    ("facebook", ("facebook.com", "fb.com", "fb.me")),
    ("instagram", ("instagram.com", "instagr.am")),
    ("x", ("twitter.com", "x.com")),
    ("youtube", ("youtube.com", "youtu.be")),
    ("tiktok", ("tiktok.com",)),
    ("whatsapp", ("wa.me", "whatsapp.com")),
    ("behance", ("behance.net", "dribbble.com")),
    ("maps", ("goo.gl/maps", "maps.app.goo.gl", "maps.google")),
)

# Words that mark a line as an address rather than a name.
ADDRESS_HINTS = (
    "road", "rd", "street", "st", "avenue", "ave", "lane", "ln", "drive",
    "mawatha", "place", "court", "block", "floor", "suite", "unit", "no.",
    "building", "tower", "plaza", "district", "province", "city", "town",
    "postcode", "zip", "p.o. box", "po box",
)

LABEL_RE = re.compile(
    r"^\s*(?:name|business|company|brand|website|site|url|email|e-mail|mail|"
    r"phone|tel|telephone|mobile|contact|address|location|linkedin|github|"
    r"facebook|instagram|twitter|x|portfolio|owner)\s*[:\-–]\s*", re.I)

_NOISE = {"n/a", "na", "none", "-", "--", "tbd", "todo", "nil", "no"}


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", str(line or "")).strip(" ,;|")


def _host(url: str) -> str:
    stripped = re.sub(r"^https?://", "", str(url or ""), flags=re.I)
    return stripped.split("/", 1)[0].lower().removeprefix("www.")


def _tidy_url(url: str) -> str:
    url = str(url or "").strip().rstrip(".,;)")
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    return url


# `Next.js` and `page.jsx` are not websites, and a two-letter ccTLD cannot
# tell itself apart from a file extension without help.
NOT_A_TLD = {"js", "ts", "jsx", "tsx", "py", "rb", "php", "json", "yml",
             "yaml", "md", "txt", "png", "jpg", "jpeg", "svg", "css", "html",
             "sh", "env", "lock", "log", "sql", "csv", "pdf"}


def is_domain(candidate: str) -> bool:
    """Whether this looks like somewhere a browser could actually go."""
    text = str(candidate or "").strip().rstrip(".,;)")
    if "/" in re.sub(r"^https?://", "", text, flags=re.I):
        return True
    tail = text.rsplit(".", 1)[-1].lower()
    return bool(tail) and tail not in NOT_A_TLD


def classify_link(url: str) -> str:
    """`github` for a repo, `website` for the customer's own site."""
    host = _host(url)
    for name, hosts in LINK_HOSTS:
        if any(host == h or host.endswith("." + h) or h in url.lower()
               for h in hosts):
            return name
    return "website"


def looks_like_address(line: str) -> bool:
    low = _clean(line).lower()
    if not low or len(low) < 8:
        return False
    words = set(re.findall(r"[a-z.]+", low))
    for hint in ADDRESS_HINTS:
        # A short hint has to be its own word: "st" is a street, but it is
        # also the middle of "full-stack".
        if hint in words or (len(hint) > 4 and hint in low):
            return True
    # "12/3, Colombo 04" — a number and a comma is usually a street line.
    return bool(re.search(r"\d", low) and "," in low and len(low) > 12)


def parse_details(text: str) -> dict:
    """Everything worth keeping, from one pasted block. Absent fields are absent."""
    raw = str(text or "")
    if not raw.strip():
        return {}

    emails, phones, links = [], [], {}
    leftover = []

    for line in raw.replace("\r", "\n").split("\n"):
        body = _clean(LABEL_RE.sub("", line))
        if not body or body.lower() in _NOISE:
            continue

        found_here = False
        for m in EMAIL_RE.finditer(body):
            value = m.group(0)
            if value not in emails:
                emails.append(value)
            found_here = True
        body_no_mail = EMAIL_RE.sub(" ", body)

        for m in URL_RE.finditer(body_no_mail):
            if not is_domain(m.group(1)):
                continue
            url = _tidy_url(m.group(1))
            if url:
                links.setdefault(classify_link(url), []).append(url)
                found_here = True
        body_no_url = URL_RE.sub(" ", body_no_mail)
        for m in BARE_DOMAIN_RE.finditer(body_no_url):
            if not is_domain(m.group(1)):
                continue
            url = _tidy_url(m.group(1))
            if url and url not in [u for v in links.values() for u in v]:
                links.setdefault(classify_link(url), []).append(url)
                found_here = True
        body_no_url = BARE_DOMAIN_RE.sub(" ", body_no_url)

        for m in PHONE_RE.finditer(body_no_url):
            number = re.sub(r"[\s().-]+", " ", m.group(1)).strip()
            if len(re.sub(r"\D", "", number)) >= 7 and number not in phones:
                phones.append(number)
                found_here = True
        rest = _clean(PHONE_RE.sub(" ", body_no_url))

        if rest and not found_here:
            leftover.append(rest)
        elif rest and len(rest) > 3:
            leftover.append(rest)

    address = [l for l in leftover if looks_like_address(l)]
    names = [l for l in leftover if l not in address]

    out = {}
    if names:
        out["name"] = names[0][:120]
        if len(names) > 1:
            out["notes"] = " · ".join(n[:120] for n in names[1:4])
    if emails:
        out["email"] = emails[0]
        if len(emails) > 1:
            out["other_emails"] = emails[1:4]
    if phones:
        out["phone"] = phones[0]
        if len(phones) > 1:
            out["other_phones"] = phones[1:4]
    if address:
        out["address"] = ", ".join(address)[:240]
    for kind, urls in links.items():
        seen = []
        for url in urls:
            if url not in seen:
                seen.append(url)
        out.setdefault("links", {})[kind] = seen[0] if len(seen) == 1 else seen
    return out


def details_summary(details: dict) -> str:
    """One readable line, for a log or a review screen."""
    parts = []
    if details.get("name"):
        parts.append(details["name"])
    if details.get("email"):
        parts.append(details["email"])
    if details.get("phone"):
        parts.append(details["phone"])
    links = details.get("links") or {}
    if links:
        parts.append(", ".join(sorted(links)))
    return " · ".join(parts)
