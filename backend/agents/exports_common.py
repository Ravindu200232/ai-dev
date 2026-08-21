"""Named imports that the target module does not export."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

LOCAL_PREFIXES = ("./", "../", "@/")
SKIP_SUFFIXES = (".css", ".scss", ".json", ".svg", ".png", ".jpg", ".webp")
CODE_SUFFIXES = (".js", ".jsx")


FRAMEWORK_EXPORTS = {

    "next/server": {"NextRequest", "NextResponse", "ImageResponse", "userAgent",
                    "userAgentFromString", "URLPattern", "after", "connection",
                    "unstable_rootParams"},
    "next/headers": {"cookies", "headers", "draftMode"},

    "next/navigation": {"redirect", "permanentRedirect", "notFound", "forbidden",
                        "unauthorized", "useRouter", "usePathname",
                        "useSearchParams", "useParams", "unstable_rethrow",
                        "useSelectedLayoutSegment", "useSelectedLayoutSegments",
                        "ReadonlyURLSearchParams", "RedirectType",
                        "ServerInsertedHTMLContext", "useServerInsertedHTML",
                        "unstable_isUnrecognizedActionError"},

    "next/cache": {"revalidatePath", "revalidateTag", "unstable_cache",
                   "unstable_noStore", "unstable_expirePath",
                   "unstable_expireTag",
                   "cacheLife", "cacheTag", "io", "refresh", "updateTag",
                   "unstable_cacheLife", "unstable_cacheTag"},
}


_REGEX_OK_AFTER = set("(,=:[!&|?{};+-*%~^")
_REGEX_OK_WORDS = {"return", "typeof", "case", "in", "of", "new", "delete",
                   "void", "instanceof", "do", "else", "yield", "await"}


def _regex_can_start(src: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and src[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    ch = src[j]
    if ch in _REGEX_OK_AFTER:
        return True
    if ch.isalnum() or ch in "_$":
        k = j
        while k >= 0 and (src[k].isalnum() or src[k] in "_$"):
            k -= 1
        return src[k + 1:j + 1] in _REGEX_OK_WORDS
    return False


def strip_noncode(src: str) -> str:
    """Comments and regex literals blanked; strings and offsets preserved."""
    out = list(src)
    n = len(src)
    i = 0
    stack: list = []

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]

        if stack and stack[-1] in "'\"":
            if c == "\\":
                i += 2
                continue

            if c == stack[-1] or c == "\n":
                stack.pop()
            i += 1
            continue

        if stack and stack[-1] == "`":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
            elif c == "$" and i + 1 < n and src[i + 1] == "{":
                stack.append("{")
                i += 2
                continue
            i += 1
            continue

        if c == "}" and stack and stack[-1] == "{":
            stack.pop()
            i += 1
            continue

        if c == "/" and i + 1 < n:
            nxt = src[i + 1]
            if nxt == "/":
                j = src.find("\n", i)
                j = n if j < 0 else j
                blank(i, j)
                i = j
                continue
            if nxt == "*":
                j = src.find("*/", i + 2)
                j = n if j < 0 else j + 2
                blank(i, j)
                i = j
                continue
            if _regex_can_start(src, i):
                end = _regex_end(src, i, n)
                if end > 0:
                    blank(i, end)
                    i = end
                    continue

        if c in "'\"`":
            stack.append(c)
            i += 1
            continue

        i += 1

    return "".join(out)


def _regex_end(src: str, i: int, n: int) -> int:
    """Index just past a regex literal starting at `i`, or 0 if it is not one."""
    j = i + 1
    in_class = False
    while j < n:
        d = src[j]
        if d == "\\":
            j += 2
            continue
        if d == "\n":
            return 0
        if d == "[":
            in_class = True
        elif d == "]":
            in_class = False
        elif d == "/" and not in_class:
            return j + 1
        j += 1
    return 0


@dataclass
class ModuleExports:
    named: set = field(default_factory=set)
    has_default: bool = False
    star_from: list = field(default_factory=list)
    named_from: dict = field(default_factory=dict)

_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:async\s+)?(?:function\s*\*?|class|const|let|var)\s+"
    r"([A-Za-z_$][\w$]*)")
_EXPORT_DESTRUCT_RE = re.compile(
    r"\bexport\s+(?:const|let|var)\s*([{\[])([^}\]]*)[}\]]\s*=")
_EXPORT_DEFAULT_RE = re.compile(r"\bexport\s+default\b")
_EXPORT_STAR_RE = re.compile(
    r"""\bexport\s*\*\s*(?:as\s+([A-Za-z_$][\w$]*)\s*)?from\s*['"]([^'"]+)['"]""")
_EXPORT_BLOCK_RE = re.compile(
    r"""\bexport\s*\{([^}]*)\}\s*(?:from\s*['"]([^'"]+)['"])?""")

__all__ = [name for name in globals() if not name.startswith("__")]
