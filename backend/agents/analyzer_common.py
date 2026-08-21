"""Post-generation audit."""
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .architect import FileStreamParser
from .commands import CommandRunner
from . import nextdocs
from .exports import (FRAMEWORK_EXPORTS, check_default_imports,
                      strip_noncode as _strip_noncode,
                       check_named_imports,
                      parse_imports, resolve_local)

log = logging.getLogger("analyzer")


SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "out", ".vite", ".agentforge",
             ".turbo", "public", "coverage"}
SOURCE_EXT = {".js", ".jsx", ".mjs", ".css", ".json", ".md"}
CODE_EXT = {".js", ".jsx", ".mjs"}


NEXT_ROOTS = ("app/", "components/", "lib/")


ROOT_SOURCE = {"middleware.js", "middleware.jsx", "instrumentation.js"}
MAX_FILE_BYTES = 200_000

REPAIRABLE_MAJOR = frozenset({
    "UNBUILT_PROMISE", "BROKEN_CONTRACT", "MISSING_PLANNED_DATA",
    "INERT_CONTROL", "ROLE_REDIRECT", "MISSING_WORKFLOW_CONTROL",
    "MISSING_ACTION_ID", "INLINE_FILE_BYTES", "UPLOAD_NOT_MULTIPART",
    "LAYOUT_CHROME", "LINT",
})


PROSE_PATH_RE = re.compile(r"`((?:app|components|lib)/[^`]+?\.jsx?)`")


PLACEHOLDER_RE = re.compile(r"[*?<>\s]|\.\.\.")


LINK_HREF_RE = re.compile(
    r"""<Link\b[^>]*?href\s*=\s*(?:["'](/[^"']*)["']|\{\s*["'](/[^"']*)["']\s*\})""")
ROUTER_PUSH_RE = re.compile(r"""router\.(?:push|replace)\(\s*["'](/[^"']*)["']""")


FETCH_URL_RE = re.compile(r"""fetch\(\s*['"](/api/[A-Za-z0-9_\-/\[\]]*)['"]""")


BCRYPT_LITERAL_RE = re.compile(r"""["'](\$2[aby]?\$\d\d\$[^"']*)["']""")

HTTP_METHOD_RE = re.compile(
    r"export\s+(?:async\s+)?(?:function\s+|const\s+)"
    r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")

SEVERITIES = ("blocker", "major", "minor")


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    path: str = ""
    fix: str = ""

    extra: list = field(default_factory=list)

    def line(self) -> str:
        where = f"{self.path}: " if self.path else ""
        return f"[{self.severity}] {where}{self.message}"


@dataclass
class AnalyzerReport:
    findings: list = field(default_factory=list)
    planned: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    routes: dict = field(default_factory=dict)
    dead_links: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    credentials: dict = field(default_factory=dict)
    written: int = 0

    def blockers(self) -> list:
        return [f for f in self.findings if f.severity == "blocker"]

    def is_clean(self) -> bool:
        return not self.blockers()

    def summary(self) -> str:
        if not self.findings:
            return "no problems found"
        by = {s: sum(1 for f in self.findings if f.severity == s)
              for s in SEVERITIES}
        return ", ".join(f"{n} {s}" for s, n in by.items() if n)

    def as_prompt_block(self, limit: int = 25) -> str:
        ranked = sorted(self.findings, key=lambda f: SEVERITIES.index(f.severity))
        lines = []
        for i, f in enumerate(ranked[:limit], 1):
            lines.append(f"{i}. {f.line()}")
            if f.fix:
                lines.append(f"   → {f.fix}")
        if len(ranked) > limit:
            lines.append(f"… and {len(ranked) - limit} more")
        return "\n".join(lines)

__all__ = [name for name in globals() if not name.startswith("__")]
