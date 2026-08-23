"""What a QA round found, in a shape both the repair loop and the UI can read."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_MESSAGE = 400
MAX_STACK = 1_400

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Failure classes worth naming, because each one has a different first move.
KINDS = [
    ("MISSING_TESTID", re.compile(
        r"Unable to find an element by: \[data-testid|"
        r"waiting for locator.*data-testid", re.I)),
    ("MISSING_ROLE", re.compile(
        r"Unable to find an accessible element|Unable to find a label", re.I)),
    ("AMBIGUOUS", re.compile(
        r"Found multiple elements|strict mode violation", re.I)),
    ("IMPORT", re.compile(
        r"Cannot find module|Failed to resolve import|"
        r"does not provide an export", re.I)),
    ("SYNTAX", re.compile(r"SyntaxError|Transform failed|Unexpected token", re.I)),
    ("TIMEOUT", re.compile(r"Test timed out|hook timed out|Timeout .*exceeded", re.I)),
    ("SERVER_DOWN", re.compile(r"ERR_CONNECTION_REFUSED|ECONNREFUSED", re.I)),
    ("HANDLER_ERROR", re.compile(r"expected 500 to be|Internal Server Error", re.I)),
    ("ASSERTION", re.compile(r"AssertionError|expected .* to |toBe|toEqual", re.I)),
]


def classify(message: str) -> str:
    """A short name for why this failed. 'RUNTIME' when nothing else fits."""
    for kind, pattern in KINDS:
        if pattern.search(message or ""):
            return kind
    return "RUNTIME"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text or ""))


@dataclass
class Failure:
    """One failing case, trimmed to what a repair turn needs to read."""

    suite: str = ""
    name: str = ""
    message: str = ""
    stack: str = ""
    kind: str = "RUNTIME"

    @classmethod
    def make(cls, suite: str, name: str, blob: str) -> "Failure":
        clean = strip_ansi(blob).strip()
        first = (clean.splitlines() or ["failed"])[0]
        return cls(suite=suite, name=name, message=first[:MAX_MESSAGE],
                   stack=clean[:MAX_STACK], kind=classify(clean))

    def as_prompt(self) -> str:
        return f"{self.suite} › {self.name}\n[{self.kind}] {self.stack}"

    def as_dict(self) -> dict:
        return {"suite": self.suite, "name": self.name, "kind": self.kind,
                "message": self.message}


@dataclass
class QAReport:
    """One round of one kind of test."""

    kind: str = "unit"                 # unit | e2e
    passed: int = 0
    failures: list = field(default_factory=list)
    ran: bool = False
    note: str = ""

    @property
    def failed(self) -> int:
        return len(self.failures)

    @property
    def green(self) -> bool:
        return self.ran and not self.failures

    def kinds(self) -> list:
        """`[(class, count)]`, commonest first — what to fix in what order."""
        counts: dict[str, int] = {}
        for failure in self.failures:
            counts[failure.kind] = counts.get(failure.kind, 0) + 1
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    def prompt(self, limit: int = 6) -> str:
        """The failures as the repair turn sees them."""
        return "\n\n".join(f.as_prompt() for f in self.failures[:limit])

    def summary(self) -> str:
        if not self.ran:
            return f"{self.kind}: did not run — {self.note or 'no report'}"
        shape = ", ".join(f"{count}× {kind}" for kind, count in self.kinds())
        return (f"{self.kind}: {self.passed} passed, {self.failed} failed"
                + (f" ({shape})" if shape else ""))

    def as_dict(self) -> dict:
        return {"kind": self.kind, "passed": self.passed, "failed": self.failed,
                "green": self.green, "note": self.note,
                "failures": [f.as_dict() for f in self.failures]}
