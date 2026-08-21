"""Running the generated tests, and turning what comes back into something."""
import json
import logging
import re
from pathlib import Path

from .spec import TestFailure

log = logging.getLogger("qa.runner")

from .runner_execute import RunnerExecutionMixin, REPORT, TIMEOUT, TARGET_COMMAND_CHARS


_KINDS = [
    ("IMPORT", re.compile(
        r"Failed to resolve import|Cannot find module|ERR_MODULE_NOT_FOUND"
        r"|does not provide an export", re.I)),

    ("SYNTAX", re.compile(
        r"SyntaxError|Transform failed|Unexpected token|Parse failure"
        r"|invalid JS syntax|Failed to parse source", re.I)),
    ("TIMEOUT", re.compile(r"Test timed out|hook timed out", re.I)),
    ("ASSERTION", re.compile(
        r"AssertionError|expected .* to |toBe|toEqual|toHaveBeenCalled", re.I)),
]


def classify(message: str) -> str:
    for kind, rx in _KINDS:
        if rx.search(message or ""):
            return kind
    return "RUNTIME"


_DIAGNOSES = [

    ("route handler crashed", re.compile(
        r"expected 500 to be [1-4]\d\d", re.I)),

    ("next/navigation not mocked", re.compile(
        r"invariant expected app router to be mounted", re.I)),

    # A spy not being called is not enough to conclude.
    ("spy-not-called", re.compile(
        r"expected \"spy\" to be called|toHaveBeenCalled.*never", re.I)),

    ("seed-data ambiguity", re.compile(r"Found multiple elements", re.I)),
    ("ObjectId vs JSON", re.compile(r"to be \{ ?Object \(buffer\)", re.I)),
    ("invented testid", re.compile(
        r"Unable to find an element by: \[data-testid", re.I)),
    ("role/name not in markup", re.compile(
        r"Unable to find an accessible element|Unable to find an element with"
        r" the role|Unable to find a label with the text", re.I)),
    ("exact copy not there", re.compile(
        r"Unable to find an element with the text", re.I)),
    ("missing helper or import", re.compile(
        r"is not a function|is not defined|No \S+ export is defined", re.I)),
    ("vi.mock hoisting", re.compile(
        r"Cannot access '\w+' before initialization", re.I)),
    ("timeout or async", re.compile(
        r"Test timed out|hook timed out|STACK_TRACE_ERROR", re.I)),
    ("presence assumption", re.compile(r"toBeInTheDocument|toBeVisible", re.I)),
]


_USELESS_TIMEOUT_RE = re.compile(r"STACK_TRACE_ERROR|Test timed out", re.I)


def timeout_hint(blob: str, duration_ms: float) -> str:
    """The advice a timed-out case needs, returned on its own."""
    if not blob or not _USELESS_TIMEOUT_RE.search(blob):
        return ""
    return ("--- what a timeout here actually means ---\n"
            "This case did not assert anything wrong; it never finished. It "
            f"ran for {int(duration_ms)}ms and was killed. Something it waited "
            "for never happened, and in this project that is nearly always one "
            "of three things:\n"
            "  1. `vi.useFakeTimers()` with an `await findBy…` or "
            "`await waitFor(…)` later in the file. Those poll the clock the "
            "fake timers froze, so they can never re-run. Fix: "
            "`vi.useFakeTimers({ shouldAdvanceTime: true })`, or drop the fake "
            "timers and pass `{ timeout: 2500 }` to waitFor.\n"
            "  2. A submit driven by `fireEvent.click` on the button. jsdom "
            "runs no default form submission, so `onSubmit` never fires and "
            "the success state never appears. Fix: "
            "`fireEvent.submit(container.querySelector('form'))`.\n"
            "  3. A `fetch` mock that resolves to nothing, so the handler "
            "awaits forever. Fix: make it resolve "
            "`{ ok: true, json: async () => ({}) }`.\n"
            "Check the file for those three in that order before changing any "
            "assertion — the assertions are probably fine.")


_OBJECTID_RE = re.compile(r"to be \{ ?Object \(buffer\)", re.I)


def objectid_hint(blob: str) -> str:
    """The string-versus-ObjectId comparison, named."""
    if not blob or not _OBJECTID_RE.search(blob):
        return ""
    return ("--- what `{ Object (buffer) }` means here ---\n"
            "The two sides are the same id in different types, so the values "
            "match and the comparison still fails. `oid()` returns a real "
            "ObjectId — an object — and anything that has been through a "
            "handler's JSON response, or through `.toString()`, is a 24-"
            "character string.\n"
            "So `expect(row._id.toString()).toBe(userId)` compares a string "
            "against an ObjectId and can never pass, however correct the code "
            "is. Convert BOTH sides: keep `const userId = String(oid())` at the "
            "top, or write `.toBe(userId.toString())` at the assertion. Do not "
            "change the handler — it is right to return a string.")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_DOM_OPEN_RE = re.compile(r"^\s*<body\b", re.M)
_LONG_CLASS_RE = re.compile(r'class="[^"]{31,}"')
_SVG_RE = re.compile(r"<svg\b.*?</svg>", re.S)


DOM_CHARS = 1600


def _compact_dom(dom: str) -> str:
    """Squeeze pretty-printed markup down to the part that carries meaning."""

    out, buf = [], ""
    for line in dom.splitlines():
        buf = f"{buf} {line.strip()}" if buf else line.rstrip()
        if buf.rstrip().endswith(">") or not buf.strip():
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    text = "\n".join(out)

    text = _SVG_RE.sub("<svg/>", text)

    text = _LONG_CLASS_RE.sub('class="..."', text)
    if len(text) > DOM_CHARS:
        text = text[:DOM_CHARS] + "\n  … (markup truncated)"
    return text


def split_dom(blob: str) -> tuple:
    """`(everything else, the compacted markup)`."""
    text = _ANSI_RE.sub("", blob or "")
    open_at = _DOM_OPEN_RE.search(text)
    if not open_at:
        return text, ""
    close_at = text.find("</body>", open_at.start())
    if close_at < 0:
        return text, ""
    end = close_at + len("</body>")
    rest = text[:open_at.start()] + text[end:]
    return rest, _compact_dom(text[open_at.start():end])


def failure_hint(blob: str, duration_ms: float = 0) -> str:
    """The advice for whichever known class this failure belongs to, or ""."""
    return timeout_hint(blob, duration_ms) or objectid_hint(blob)


def diagnose(message: str) -> str:
    """A short, human name for why one case failed. '' when nothing fits."""
    for name, rx in _DIAGNOSES:
        if rx.search(message or ""):
            return name
    return "value mismatch" if "AssertionError" in (message or "") else ""


def diagnose_all(failures) -> list:
    """`[(class, count), …]`, commonest first, for a round's failures."""
    counts = {}
    for f in failures or []:
        name = diagnose(getattr(f, "message", "") or "")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


class VitestRunner(RunnerExecutionMixin):
    """`npx vitest run`, parsed."""

    def __init__(self, project_dir, *, cmd=None, callbacks=None, session=None):
        self.project_dir = Path(project_dir)
        self.cmd = cmd
        self.cb = callbacks or {}
        self.qa = session

    def _fire(self, name, *a):
        fn = self.cb.get(name)
        if fn and callable(fn):
            try:
                fn(*a)
            except Exception as e:
                log.warning(f"callback {name} failed: {e}")

    def _log(self, lvl, txt):
        self._fire("on_log", lvl, txt)
        log.info(txt)

    def run(self, paths=None):
        """`(passed, failures, ok)`."""

        from .harness import NPM_LOCK, npm_busy
        if npm_busy():
            self._log("INFO", "   ⏸ waiting for npm to finish before running "
                              "the tests")
        with NPM_LOCK:
            return self._run_locked(paths)

    GONE_RE = re.compile(
        r"Cannot find (?:package|module) ['\"][^'\"]*"
        r"(?:vitest|jsdom|@testing-library[/\\])|"
        r"Failed to (?:load|resolve) .*vitest|"
        r"'vitest' is not recognized|vitest: not found",
        re.I)

    def _runner_gone(self, output: str) -> bool:
        """Whether a run failed because the runner itself is not there."""
        if not (self.project_dir / "node_modules" / "vitest").is_dir():
            return True
        return bool(self.GONE_RE.search(output or ""))

    MISSING_MODULE_RE = re.compile(r"Cannot find module ['\"]([^'\"]+)['\"]")

    @staticmethod
    def collected_nothing(data: dict) -> bool:
        """A report that lists test files and contains no test cases."""
        files = data.get("testResults") or []
        if not files:
            return False
        total = data.get("numTotalTests")
        if total is None:
            total = sum(len(f.get("assertionResults") or []) for f in files)
        return int(total or 0) == 0

    def _heal_environment(self, output: str) -> bool:
        """Install what the test environment could not find."""
        if not self.cmd:
            return False
        names, seen = [], set()
        for raw in self.MISSING_MODULE_RE.findall(output or ""):
            name = str(raw).strip()
            if (not name or name in seen or name.startswith((".", "/", "@/"))
                    or ":" in name or "\\" in name):
                continue
            seen.add(name)
            names.append(name)
        if not names:
            return False

        self._log("WARN", "   📦 the test environment is missing "
                          + ", ".join(names[:4])
                          + " — installing and running again")
        res = self.cmd.run(["npm", "install", "--no-audit", "--no-fund",
                            "--loglevel=error", *names[:4]], timeout=600)
        ok = getattr(res, "code", 1) == 0
        if not ok:
            self._log("WARN", "   ⚠ that install did not succeed")
        return ok

    def _reinstall_runner(self) -> bool:
        """Put the test toolchain back."""
        if not self.cmd:
            return False
        try:
            from .harness import TestHarness
            h = TestHarness(self.project_dir, callbacks=self.cb, cmd=self.cmd)
            h.materialise()
            return bool(h.install())
        except Exception as e:
            self._log("WARN", f"   ⚠ could not reinstall the test runner: {e}")
            log.exception("qa: reinstall runner")
            return False

    def _merge_into_report(self, part: dict) -> None:
        """Fold a targeted run's results back into the whole-app report."""
        full = self.project_dir / REPORT
        try:
            old = json.loads(full.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            old = None
        if not isinstance(old, dict) or not isinstance(old.get("testResults"), list):
            # Nothing to merge into — this run IS the report.
            old = None

        if old is None:
            merged = dict(part)
        else:
            fresh = {self._rel(s.get("name") or s.get("testFilePath") or ""): s
                     for s in (part.get("testResults") or [])}
            kept = [s for s in old["testResults"]
                    if self._rel(s.get("name") or s.get("testFilePath") or "")
                    not in fresh]
            merged = dict(old)
            merged["testResults"] = kept + list(fresh.values())

        # The counters are recomputed from the suites rather than added.
        cases = [c for s in merged["testResults"]
                 for c in (s.get("assertionResults") or [])]
        passed = sum(1 for c in cases if c.get("status") == "passed")
        failed = sum(1 for c in cases if c.get("status") == "failed")
        merged.update({
            "numTotalTestSuites": len(merged["testResults"]),
            "numTotalTests": len(cases),
            "numPassedTests": passed,
            "numFailedTests": failed,
            "numPendingTests": len(cases) - passed - failed,
            "numFailedTestSuites": sum(1 for s in merged["testResults"]
                                       if s.get("status") == "failed"),
            "numPassedTestSuites": sum(1 for s in merged["testResults"]
                                       if s.get("status") == "passed"),
            "success": failed == 0,
        })
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(json.dumps(merged), encoding="utf-8")
        except OSError as e:
            log.debug(f"merging into {REPORT}: {e}")

    def _parse(self, data):
        passed, failures = 0, []

        self.last_cases = {}
        for suite in data.get("testResults") or []:
            rel = self._rel(suite.get("name") or suite.get("testFilePath") or "")
            target = self.qa.target_of(rel) if self.qa else ""
            stale = bool((self.qa.manifest.get(rel, {}) if self.qa else {}).get("stale"))

            if suite.get("status") == "failed" and not (suite.get("assertionResults") or []):
                msg = (suite.get("message") or "the test file could not be loaded").strip()
                failures.append(TestFailure(
                    test_file=rel, target=target, name="(the file did not load)",
                    message=msg.splitlines()[0][:400], stack=msg[:2000],
                    kind=classify(msg), stale=stale))
                continue

            for case in suite.get("assertionResults") or []:
                name = (case.get("fullName") or case.get("title") or "?")[:200]
                status = case.get("status") or "unknown"
                self.last_cases.setdefault(rel, {})[name] = status
                if status == "passed":
                    passed += 1
                    continue
                if status in ("skipped", "pending", "todo"):
                    continue
                msgs = case.get("failureMessages") or []
                blob = "\n".join(msgs)
                rest, dom = split_dom(blob)
                failures.append(TestFailure(
                    test_file=rel, target=target,
                    name=name,
                    message=(rest.strip().splitlines() or ["failed"])[0][:400],

                    stack=rest[:2000], kind=classify(blob), stale=stale,
                    dom=dom,
                    hint=failure_hint(blob, case.get("duration") or 0)))
        return passed, failures

    def case_names(self) -> set:
        """`{(test_file, case_name)}` for every case the last run saw."""
        return {(f, n) for f, cases in getattr(self, "last_cases", {}).items()
                for n in cases}

    def skipped(self) -> list:
        """`[(test_file, case_name)]` for cases the last run did not execute."""
        return sorted((f, n)
                      for f, cases in getattr(self, "last_cases", {}).items()
                      for n, st in cases.items()
                      if st in ("skipped", "pending", "todo"))

    def vanished(self, before: set) -> list:
        """Cases that existed in `before` and are not in the suite any more."""
        return sorted(before - self.case_names())

    def _rel(self, abs_path):
        """The project-relative path, which is the manifest's key."""
        raw = str(abs_path or "")
        norm = raw.replace("\\", "/").lstrip("./")

        if not Path(raw).is_absolute():
            return norm
        try:
            return str(Path(raw).resolve().relative_to(
                self.project_dir.resolve())).replace("\\", "/")
        except Exception:
            pass

        i = norm.find("/tests/")
        if i >= 0:
            return norm[i + 1:]
        return norm.split("/")[-1]

    def quarantine(self, test_file, why=""):
        """Move a test AgentForge could not make trustworthy out of the run."""
        src = self.project_dir / test_file
        if not src.is_file():
            return False
        dst = self.project_dir / "tests" / "quarantine" / Path(test_file).name
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            body = src.read_text(encoding="utf-8", errors="replace")
            note = f"// QUARANTINED by AgentForge: {why}\n" if why else ""
            dst.write_text(note + body, encoding="utf-8")
            src.unlink()
        except Exception as e:
            self._log("WARN", f"   ⚠ could not quarantine {test_file}: {e}")
            return False
        if self.qa:
            self.qa.manifest.pop(test_file, None)
            self.qa.report.quarantined.append(test_file)
            self.qa._save_manifest()
        self._log("WARN", f"   🚧 quarantined {test_file} — {why}")
        return True
