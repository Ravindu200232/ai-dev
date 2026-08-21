"""Unit-test planning, authoring, execution verification and one-case repair."""
from .author_common import *


class UnitAuthorWriteMixin:
    def __init__(self, arch, project_dir=None, *, callbacks=None,
                 analyzer=None, session=None):
        self.arch = arch
        self.project_dir = project_dir or arch.project_dir
        self.cb = callbacks or {}
        self.az = analyzer
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

    def _idea(self):
        """The app's idea and approved plan, copied out of the build conversation."""
        try:
            convo = getattr(self.arch, "convo", None) or []
            if len(convo) > 1 and convo[1].get("role") == "user":
                return convo[1]["content"][:4000]
        except Exception:
            pass
        return (getattr(self.arch, "plan_md", "") or "")[:4000]

    @staticmethod
    def _preamble_note(target_src: str) -> str:
        """The mocks this file's test needs, worked out and handed over."""
        from .session import mock_line, required_mocks
        needed = required_mocks(target_src)
        if not needed:
            return ""
        lines = "\n".join(mock_line(mod, helper) for mod, helper in needed)
        return ("\n\nThis file needs these, at the very top — nothing it does "
                f"can be tested without them:\n{lines}")

    def _phase_goal(self, phase):
        """The phase's own title and goal, or "" when there is no phase."""
        if not phase:
            return ""
        try:
            ph = (self.arch.plan.get("phases") or [])[phase - 1]
            bits = [ph.get("title", ""), ph.get("goal", ""), ph.get("done_when", "")]
            return " — ".join(b for b in bits if b)[:400]
        except Exception:
            return ""

    def _brief_for(self, path: str) -> str:
        """What the plan asked this file to be, in the plan's own words."""
        try:
            for ph in (self.arch.plan.get("phases") or []):
                for f in ph.get("files") or []:
                    if f.get("path") != path:
                        continue
                    out = []
                    if f.get("purpose"):
                        out.append(f"  purpose: {str(f['purpose'])[:220]}")
                    secs = [str(x) for x in (f.get("sections") or [])][:8]
                    if secs:
                        out.append("  it should show, top to bottom:")
                        out += [f"    - {x[:150]}" for x in secs]
                    acts = [str(x) for x in (f.get("actions") or [])][:8]
                    if acts:
                        out.append("  it should do:")
                        out += [f"    - {x[:150]}" for x in acts]
                    return "\n".join(out)
        except Exception:
            pass
        return ""

    def _testid_note(self, target_rel: str) -> str:
        """The test ids this file really sets, or that it sets none."""
        try:
            ids = self.testids_for_target(target_rel)
        except Exception as e:
            log.debug(f"testids for {target_rel}: {e}")
            return ""
        if not ids:
            return ("\n\nThis file sets NO `data-testid`. Do not call "
                    "getByTestId/findByTestId/queryByTestId for it at all - "
                    "query by role and accessible name, or by label.\n")
        return ("\n\nTest ids this file and the components it renders really "
                "set:\n  " + ", ".join(ids)
                + "\nThese are the only values `getByTestId` may be given for "
                  "this file. If the control you need is not on this list, it "
                  "has no test id - query it by role and accessible name "
                  "instead. Never invent one, and never build one from a "
                  "variable.\n")

    def write_for(self, targets, phase=0, max_reads=MAX_READS):
        """Write a test for each target."""
        if not targets:
            return []

        assigned = {t.test_path: t for t in targets}
        blocks = []
        for t in targets:
            body = (self.qa.read_source(t.path) if self.qa else None) or ""
            if not body.strip():
                continue
            brief = self._brief_for(t.path)
            blocks.append(f"--- {t.path} ---\n{body}\n\n"
                          + (f"Plan context for understanding intent only — "
                             f"DO NOT assert a promised feature that is absent "
                             f"from the source. Product completeness is owned "
                             f"by the Analyzer/E2E gates, not by this unit "
                             f"test:\n{brief}\n\n"
                             if brief else "")
                          + self._testid_note(t.path)
                          + f"Write its test at: {t.test_path}"
                          + self._preamble_note(body))
        if not blocks:
            return []

        goal = self._phase_goal(phase)
        convo = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"## The app\n{self._idea()}\n\n"
                + (f"## This phase\n{goal}\n\n" if goal else "")
                + "## Files to test — read them, then write one test file "
                  "each\n\n"
                + "\n\n".join(blocks)
                + "\n\nWrite the test files now, one <write_file> block each."},
        ]

        written, rejected, reads = [], [], 0

        self._advice = {}
        budget = self.az._budget_chars() if self.az else 40_000

        while True:
            raw = []
            parser = FileStreamParser(
                on_text=lambda t: raw.append(t),
                on_file_start=lambda p: self._fire("on_file_start", p),
                on_file_token=lambda tok: None,
                on_file_end=lambda p, c: self._accept(p, c, assigned, written,
                                                      rejected, phase))
            try:
                self.arch._stream(convo, parser.feed, temperature=TEMPERATURE,
                                  model=QASession.model_for(self.qa, self.arch),
                                  timeout=CALL_BUDGET)
            except Exception as e:
                self._log("WARN", f"   ⚠ test author failed: {e}")
                parser.close()
                break
            parser.close()
            reply = "".join(raw)
            convo.append({"role": "assistant", "content": reply})

            wanted = self.az.READ_RE.findall(reply) if self.az else []
            used = sum(len(m["content"]) for m in convo)
            if not wanted or reads >= max_reads or used >= budget:
                break
            served = []
            for rel in wanted[:4]:
                reads += 1
                served.append(f"--- {rel} ---\n{self.az._read_for_model(rel)}")
            convo.append({"role": "user",
                          "content": "\n\n".join(served)
                                     + "\n\nNow write the test files."})

        if rejected:
            self._log("WARN", f"   ⛔ ignored {len(rejected)} off-list write(s): "
                              f"{', '.join(rejected[:3])}")

        missed = [t for p, t in assigned.items() if p not in written]
        if missed:
            names = ", ".join(t.path for t in missed[:3])
            self._log("WARN", f"   ↻ {len(missed)} file(s) got no test — "
                              f"asking again for {names}")
            static_notes = []
            for t in missed:
                note = self._advice.get(t.test_path, "")
                if note:
                    static_notes.append(f"- `{t.test_path}`: {note}")
            advice_block = ("\n\nStatic preflight rejected the first draft(s):\n"
                            + "\n".join(static_notes)
                            if static_notes else "")
            convo.append({"role": "user", "content":
                "You did not write a usable test for "
                + ", ".join(f"`{t.path}` (at `{t.test_path}`)" for t in missed)
                + ". Write "
                + ("it" if len(missed) == 1 else "them")
                + " now, querying ONLY behavior and selectors that actually "
                  "appear in the source you were shown. Do not turn plan-only "
                  "promises into red unit tests; Analyzer/E2E owns missing "
                  "features."
                + advice_block
                + "\nOne <write_file> block each, nothing else."})
            parser = FileStreamParser(
                on_text=lambda t: None,
                on_file_start=lambda p: self._fire("on_file_start", p),
                on_file_token=lambda tok: None,
                on_file_end=lambda p, c: self._accept(p, c, assigned, written,
                                                      rejected, phase))
            try:
                self.arch._stream(convo, parser.feed, temperature=TEMPERATURE,
                                  model=QASession.model_for(self.qa, self.arch),
                                  timeout=CALL_BUDGET)
            except Exception as e:
                self._log("WARN", f"   ⚠ retry failed: {e}")
            parser.close()
            still = [t.path for p, t in assigned.items() if p not in written]
            if still:
                self._log("WARN", f"   ⚠ still untested: {', '.join(still[:4])}")

        self._verify(convo, assigned, written, rejected, phase)
        self._suspects(written)
        return written

    def _runner(self):
        """A VitestRunner, or None if there is nothing to run tests with."""
        cmd = getattr(self.qa, "cmd", None) if self.qa else None
        if not cmd:
            return None

        from .runner import VitestRunner
        return VitestRunner(self.project_dir, cmd=cmd, callbacks=self.cb,
                            session=self.qa)

    # A failure that is the ENVIRONMENT, not the test.
    _UNSETTLED_RE = re.compile(
        r"Cannot read properties of null \(reading 'use[A-Z]"
        r"|Failed to load url|ERR_MODULE_NOT_FOUND"
        r"|Failed to resolve import|Cannot find (?:module|package)"
        r"|does not provide an export named"
        r"|ENOENT: no such file|Error: Cannot find module"
        r"|is not a function\b.*(?:import|require)"
        r"|ECONNREFUSED|EPERM|EBUSY", re.I)

    @classmethod
    def _looks_unsettled(cls, failures) -> bool:
        """True when most of this run's failures are the module graph, not tests."""
        hits = sum(1 for f in (failures or [])
                   if cls._UNSETTLED_RE.search(str(getattr(f, "message", ""))
                                               + " " + str(getattr(f, "stack", ""))[:400]))
        return bool(failures) and hits >= max(1, len(failures) // 2)

    def _target_unfinished(self, t) -> str:
        """The planned file the test depends on that is not on disk yet, or ""."""
        arch = getattr(self, "arch", None)
        if not arch or not t or not getattr(t, "path", None):
            return ""
        try:
            outstanding = {f["path"].lstrip("./").replace("\\", "/")
                           for f in arch._outstanding()}
        except Exception:
            return ""
        if not outstanding:
            return ""
        src = (self.qa.read_source(t.path) or "") if self.qa else ""
        for m in re.finditer(r"""from\s+['"]@/([^'"]+)['"]""", src):
            rel = m.group(1)
            for cand in (rel, rel + ".jsx", rel + ".js",
                         rel + "/index.jsx", rel + "/index.js"):
                if cand in outstanding:
                    return cand
        return ""

    @staticmethod
    def _missing_packages(failures) -> list:
        """Packages the run says it could not resolve, across every failure."""
        from .harness import TestHarness
        text = "\n".join(f"{getattr(f, 'message', '')}\n{getattr(f, 'stack', '')}"
                         for f in (failures or []))
        return TestHarness.missing_packages(text)

    def _install_missing(self, packages) -> bool:
        from .harness import TestHarness
        cmd = getattr(self.qa, "cmd", None) if self.qa else None
        if not cmd:
            return False
        try:
            h = TestHarness(self.project_dir, callbacks=self.cb, cmd=cmd)
            return h.install_missing(packages)
        except Exception as e:                              # noqa: BLE001
            self._log("WARN", f"   ⚠ could not install {packages}: {e}")
            return False

    def _verify(self, convo, assigned, written, rejected, phase):
        """Run what was just written, and hand back what actually happened."""
        if getattr(self.qa, "defer_execution", False):
            if written:
                self._log("INFO", f"   🧪 authored {len(written)} test file(s) — execution deferred until the post-build unit stage")
            return

        runner = self._runner()
        if runner is None or not written:
            return

        passed, failures, ok = runner.run(paths=written)

        if failures:
            missing = self._missing_packages(failures)
            if missing and self._install_missing(missing):
                passed, failures, ok = runner.run(paths=written)

        # Wait for the environment, not a stopwatch.
        from .harness import npm_busy
        deadline = time.time() + SETTLE_MAX_S
        wait = 3
        while failures and self._looks_unsettled(failures) and time.time() < deadline:
            busy = npm_busy()
            self._log("INFO", f"   ⏳ the module graph is still settling"
                              f"{' — npm is installing' if busy else ''} — "
                              f"waiting {wait}s and running again before "
                              f"judging these")
            time.sleep(wait)
            wait = min(wait * 2, 20)
            passed, failures, ok = runner.run(paths=written)
            if not ok:
                break

        if failures and self._looks_unsettled(failures):
            envs = [f for f in failures
                    if self._UNSETTLED_RE.search(str(getattr(f, "message", ""))
                                                 + str(getattr(f, "stack", ""))[:400])]
            held = sorted({getattr(f, "test_file", "") for f in envs if getattr(f, "test_file", "")})
            self._log("INFO", f"   ⏸ {len(held)} test file(s) fail only on "
                              f"imports that are not there yet — leaving them "
                              f"for the unit stage rather than rewriting them: "
                              f"{', '.join(p.split('/')[-1] for p in held[:4])}")
            failures = [f for f in failures if f not in envs]

        if not ok or (passed == 0 and not failures):
            spent = getattr(getattr(self.qa, "cmd", None), "calls", None)
            cap = getattr(getattr(self.qa, "cmd", None), "max_calls", None)
            why = ""
            if spent is not None and cap is not None and spent >= cap:
                why = f" — the {cap}-command budget is spent"
            self._log("WARN", f"   ⚠ the new tests did not run{why} — leaving "
                              f"them for the unit stage")
            return

        by_file = {}
        for f in failures:
            by_file.setdefault(f.test_file, []).append(f)

        for p in self._advice:
            if p in written:
                by_file.setdefault(p, [])

        if not by_file:
            self._log("INFO", f"   ✅ {len(written)} new test file(s) pass "
                              f"({passed} case(s))")
            return

        self._log("WARN", f"   ↻ {len(by_file)} of {len(written)} new test "
                          f"file(s) failing — fixing them")

        stuck, lock = [], threading.Lock()

        def fix(path):
            ok = self._fix_one(path, assigned.get(path), by_file[path],
                               runner, written, rejected, phase, assigned, lock)
            if not ok:
                with lock:
                    stuck.append(path)

        with ThreadPoolExecutor(max_workers=QA_FIX_WORKERS) as pool:
            list(pool.map(fix, sorted(by_file)))
        if stuck:
            self._log("WARN", f"   ⚠ {len(stuck)} test file(s) still failing "
                              f"after {MAX_VERIFY_ROUNDS} round(s): "
                              f"{', '.join(p.split('/')[-1] for p in stuck[:4])}")

    def _fix_one(self, path, t, fails, runner, written, rejected, phase,
                 assigned, lock=None) -> bool:
        """One test file, fixed and re-run until it passes or the rounds run out."""
        src = (self.qa.read_source(t.path) or "") if (self.qa and t) else ""

        missing = self._target_unfinished(t)
        if missing:
            self._log("INFO", f"   ⏸ {path.split('/')[-1]}: waits for "
                              f"{missing}, which is not written yet — leaving "
                              f"it for the unit stage")
            return False

        start_body = (self.qa.read_source(path) or "") if self.qa else ""
        best = (len(fails or []), start_body) if start_body else None
        last_sig = self._failure_signature(fails)

        for rnd in range(1, MAX_VERIFY_ROUNDS + 1):
            body = (self.qa.read_source(path) or "") if self.qa else ""
            if not body:
                return False
            convo = [
                {"role": "system", "content": SYSTEM},
                {"role": "user",
                 "content": self._fix_prompt(path, t, src, body, fails)},
            ]
            parser = FileStreamParser(
                on_text=lambda x: None,
                on_file_start=lambda p: self._fire("on_file_start", p),
                on_file_token=lambda tok: None,
                on_file_end=lambda p, c: self._accept(p, c, assigned, written,
                                                      rejected, phase, lock))
            try:
                self.arch._stream(convo, parser.feed, temperature=TEMPERATURE,
                                  model=QASession.model_for(self.qa, self.arch),
                                  timeout=CALL_BUDGET)
            except Exception as e:
                self._log("WARN", f"   ⚠ {path}: fix round {rnd} failed: {e}")
                parser.close()
                return False
            parser.close()

            passed, failures, ok = runner.run(paths=[path])
            if not ok:
                return False
            fails = failures
            if not failures and passed:

                note = self._advice.get(path)
                self._log("INFO", f"   ✅ {path.split('/')[-1]} — "
                                  f"{passed} case(s) pass (round {rnd})"
                                  + (f"; note: {note}" if note else ""))
                return True

            fixed_body = (self.qa.read_source(path) or "") if self.qa else ""
            count = len(failures)
            if best is None or count < best[0]:
                best = (count, fixed_body)
            elif count > best[0] and fixed_body != best[1]:
                self._log("WARN", f"   ↩ {path.split('/')[-1]}: round {rnd} "
                                  f"made it worse ({count} vs {best[0]}) — "
                                  f"keeping the better version")
                self._restore(path, t, best[1], phase)

            sig = self._failure_signature(failures)
            if sig and sig == last_sig:
                self._log("INFO", f"   ⏹ {path.split('/')[-1]}: same "
                                  f"{count} failure(s) as the last round — "
                                  f"stopping instead of repeating it")
                break
            last_sig = sig

        if best is not None:
            current = (self.qa.read_source(path) or "") if self.qa else ""
            if current and current != best[1]:
                self._restore(path, t, best[1], phase)
        return False
