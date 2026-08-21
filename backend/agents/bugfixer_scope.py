"""Editable-scope policy, evidence reading and import/export diagnosis."""
from .bugfixer_common import *
from .features_common import safe_change_path


class BugFixerScopeMixin:
    def __init__(self, arch, project_dir=None, *, callbacks=None,
                 session=None, runner=None, model=None):
        self.arch = arch
        self.project_dir = project_dir or arch.project_dir
        self.cb = callbacks or {}
        self.qa = session
        self.runner = runner

        self.model = model or None
        self.app_writes = set()
        self.verdicts = []

        self.refusals = {}
        # Test files whose prompt hid passing-case bodies
        self._collapsed = set()

        self._lock = threading.Lock()

    def _fire(self, name, *a):
        fn = self.cb.get(name)
        if fn and callable(fn):
            try:
                fn(*a)
            except Exception as e:
                log.warning(f"callback {name} failed: {e}")

    def _log(self, lvl, txt):

        if self.cb and self.cb.get("on_log"):
            self._fire("on_log", lvl, txt)
            return
        log.info(txt)

    def _read(self, rel):
        if self.qa:
            return self.qa.read_source(rel)
        try:
            fp = self.arch._safe_path(rel)
            return fp.read_text(encoding="utf-8", errors="replace") if fp.is_file() else None
        except Exception:
            return None

    def editable(self, rel) -> bool:
        """Whether `rel` may be rewritten to satisfy a test."""
        rel = (rel or "").strip().lstrip("./").replace("\\", "/")
        if not rel or rel in NEVER_CODE:
            return False
        if rel in getattr(self.arch, "NEXT_PROTECTED", frozenset()):
            return False
        if not rel.startswith(("app/", "components/", "lib/")):
            return False
        if rel.startswith("app/api/auth/"):
            return False
        return rel.endswith((".js", ".jsx"))

    _E2E_PRIVILEGED_RUNTIME = frozenset({
        "lib/auth.js", "lib/auth-client.js", "app/api/auth/[...all]/route.js",
    })

    def runtime_editable(self, rel, privileged_paths=None) -> bool:
        """Whether an evidence-driven runtime repair may edit this source path."""
        return safe_change_path(rel)

    @staticmethod
    def _threw_in(f, target: str) -> bool:
        """Did `target` throw, or did the test?"""
        if not target:
            return False
        want = target.replace("\\", "/").lstrip("./")
        for line in (f.stack or "").splitlines():
            line = line.strip()
            if not line.startswith("at "):
                continue
            norm = line.replace("\\", "/")
            if "node_modules" in norm or "node:internal" in norm:
                continue
            if "/tests/" in norm:
                return False
            if want in norm:
                return True
        return False

    _PARSE_PATH_RE = re.compile(
        r"((?:[A-Za-z]:)?[A-Za-z0-9_./\\-]+\.(?:jsx?|tsx?))\s*:\s*\d+\s*:\s*\d+")

    def _unparseable_file(self, failures) -> str:
        """The project-relative path the transform error names, or ''."""
        root = str(self.project_dir).replace("\\", "/").rstrip("/")
        for f in failures:
            for blob in (getattr(f, "message", ""), getattr(f, "stack", "")):
                for hit in self._PARSE_PATH_RE.findall(blob or ""):
                    rel = hit.replace("\\", "/")
                    if rel.lower().startswith(root.lower()):
                        rel = rel[len(root):]
                    rel = rel.lstrip("/")
                    if rel.startswith("./"):
                        rel = rel[2:]

                    if rel and (self.project_dir / rel).is_file():
                        return rel
        return ""

    def prior(self, failures, *, build_ok=True, round_no=1):
        """`(verdict, reason, action)` when Python already knows, else."""
        if not failures:
            return ("test", "there is nothing to repair", "quarantine")
        f = failures[0]
        kinds = {x.kind for x in failures}
        target = (f.target or "").strip()

        if "SYNTAX" in kinds:
            broken = self._unparseable_file(failures)
            if broken and broken != f.test_file:
                if self.editable(broken):
                    return ("code", f"{broken} does not parse — the test is "
                                    f"fine and that file is what has to be "
                                    f"fixed", "model")
                return ("test", f"{broken} does not parse and is not a file "
                                f"that may be edited", "report")
            return ("test", "the test file does not parse", "quarantine")

        if "CRASH" in kinds:
            if target and self.editable(target):
                return ("code", f"{target} returned an error rather than a "
                                f"page — that is not an assertion anyone can "
                                f"argue with", "model")
            return ("test", "the crash is not in a file that may be edited",
                    "report")

        if "IMPORT" in kinds and build_ok:
            return ("", "the build is green, so every module in the app "
                        "resolves — this import names something that does "
                        "not exist", "model")

        if not target:
            return ("test", "this failure is not attributable to any app file",
                    "model")

        if not self.editable(target):
            return ("test", f"{target} is generated by AgentForge and is not "
                            f"editable", "model")

        if not (self._read(target) or "").strip():
            return ("test", f"{target} is missing or empty", "model")

        if any(x.kind == "RUNTIME" and self._threw_in(x, target)
               for x in failures):
            return ("code", f"{target} threw while the test ran, rather than "
                            f"an assertion failing — the component is what "
                            f"broke", "model")

        if f.stale and round_no <= 1:
            return ("", f"{target} was rewritten after this test was "
                            f"written", "model")

        missing = self.missing_export(f.test_file, target)
        if missing:
            return ("", f"{target} does not export "
                            f"{', '.join(sorted(missing))}", "model")

        if not self.imports_target(f.test_file, target):
            avail = self.exports_of(target)

            methods = sorted(avail & HTTP_METHODS)
            names = ", ".join(methods or sorted(avail - {"dynamic", "revalidate",
                                                         "runtime", "fetchCache"})[:4])
            return ("", f"this test never imports anything from {target} — "
                            f"it has to `import {{ {names} }}` from it and pass "
                            f"the function to postJson/getJson, not a URL",
                    "model")

        # These diagnoses are mechanical properties of the generated
        try:
            from qa_agent.runner import diagnose
            diagnosed = {diagnose(str(getattr(x, "message", "")))
                         for x in failures}
            test_only = {
                "seed-data ambiguity", "invented testid",
                "role/name not in markup", "exact copy not there",
                "next/navigation not mocked", "vi.mock hoisting",
            }
            if diagnosed and diagnosed <= test_only:
                names = ", ".join(sorted(diagnosed))
                return ("test", f"Vitest evidence is a generated-test "
                                f"mechanics failure ({names}), not proof that "
                                f"{target} is wrong", "model")
        except Exception:
            pass

        return (None, "", "model")

    def _files_for(self, *rels) -> dict:
        out = {}
        for rel in rels:
            src = self._read(rel)
            if src is not None:
                out[rel] = src
        return out

    def exports_of(self, target) -> set:
        """The names `target` offers, for evidence the model can act on."""
        try:
            return effective_exports(target, self._files_for(target)) or set()
        except Exception:
            return set()

    def imports_target(self, test_file, target) -> bool:
        """Whether the test imports anything at all from the file it is about."""
        body = self._read(test_file)
        if not body or not target:
            return True
        try:
            files = self._files_for(test_file, target)
            if target not in files:

                return True
            for stmt in parse_imports(body):
                if resolve_local(test_file, stmt.spec, files) == target:
                    return True

            stem = re.sub(r"\.jsx?$", "", target)
            return bool(re.search(rf"from\s+['\"]@?/?{re.escape(stem)}(\.jsx?)?['\"]",
                                  body))
        except Exception as e:
            log.debug(f"imports_target {test_file}: {e}")
            return True

    def missing_export(self, test_file, target) -> set:
        """Names the test imports from its target that the target does not export."""
        body = self._read(test_file)
        if not body:
            return set()
        try:
            files = {}
            for rel in (test_file, target):
                src = self._read(rel)
                if src is not None:
                    files[rel] = src
            avail = None
            for stmt in parse_imports(body):
                if not stmt.names:
                    continue
                resolved = resolve_local(test_file, stmt.spec, files)
                if resolved != target:
                    continue
                if avail is None:

                    avail = effective_exports(target, files)
                    if avail is None:
                        return set()

                gap = {imported for imported, _local in stmt.names
                       if imported not in avail}
                if gap:
                    return gap
        except Exception as e:
            log.debug(f"missing_export {test_file}: {e}")
        return set()
