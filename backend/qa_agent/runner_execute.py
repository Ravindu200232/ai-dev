"""Vitest command execution and large targeted-run batching."""
import hashlib
import json
import logging

REPORT = ".agentforge/qa/vitest.json"
TIMEOUT = 180
TARGET_COMMAND_CHARS = 3000

log = logging.getLogger("qa.runner")


class RunnerExecutionMixin:
    def _vitest_command(self, paths, rel: str) -> str:
        only = ""
        if paths:
            only = " " + " ".join(str(p).replace("\\", "/") for p in paths)
        # The disk preflight below owns dependency availability.
        return f"npx vitest run{only} --reporter=json --outputFile={rel}"

    def _path_batches(self, paths) -> list:
        """Split a targeted re-run by command size, never by test semantics."""
        paths = [str(p).replace("\\", "/") for p in (paths or [])]
        if not paths:
            return []
        batches, current = [], []
        for path in paths:
            trial = current + [path]
            # Use the longest report suffix we generate as the estimate.
            probe = self._vitest_command(
                trial, ".agentforge/qa/vitest-0123456789.json")
            if current and len(probe) > TARGET_COMMAND_CHARS:
                batches.append(current)
                current = [path]
            else:
                current = trial
        if current:
            batches.append(current)
        return batches

    def _run_path_batches(self, paths, repair=True):
        batches = self._path_batches(paths)
        if len(batches) <= 1:
            return self._run_locked(paths, repair=repair, allow_batch=False)
        self._log("INFO", f"   🧪 targeted re-run spans {len(paths)} test files — "
                          f"running {len(batches)} safe command batch(es)")
        passed_total, failures_all, cases = 0, [], {}
        for batch in batches:
            passed, failures, ok = self._run_locked(
                batch, repair=repair, allow_batch=False, allow_fallback=False)
            if not ok:
                self._log("WARN", "   ↪ a targeted batch produced no report — "
                                  "running the complete suite once instead")
                return self._run_locked(None, repair=repair, allow_batch=False,
                                        allow_fallback=False)
            passed_total += passed
            failures_all.extend(failures)
            for file_name, values in getattr(self, "last_cases", {}).items():
                cases[file_name] = dict(values)
        self.last_cases = cases
        return passed_total, failures_all, True

    def _run_locked(self, paths=None, repair=True, *, allow_batch=True,
                    allow_fallback=True):

        rel = REPORT
        if paths:
            tag = hashlib.sha1(
                "|".join(sorted(str(p) for p in paths)).encode()
            ).hexdigest()[:10]
            rel = f".agentforge/qa/vitest-{tag}.json"

        command = self._vitest_command(paths, rel)
        if paths and allow_batch and len(command) > TARGET_COMMAND_CHARS:
            return self._run_path_batches(paths, repair=repair)

        out = self.project_dir / rel
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                out.unlink()
        except Exception as e:
            log.debug(f"clearing {rel}: {e}")

        if not self.cmd:
            return 0, [], False

        real_project = (self.project_dir / "package.json").is_file()
        runner_missing = not (self.project_dir / "node_modules" / "vitest").is_dir()
        if real_project and runner_missing:
            if not repair:
                self._log("ERROR", "   ❌ the test runner is unavailable after repair — "
                                  "refusing an implicit npx download")
                return 0, [], False
            self._log("WARN", "   📦 the test runner is not installed yet — "
                              "installing it before running the suite")
            if not self._reinstall_runner():
                self._log("ERROR", "   ❌ the test runner could not be installed")
                return 0, [], False

        res = self.cmd.run(command, timeout=TIMEOUT)

        data = None
        try:
            if out.is_file():
                data = json.loads(out.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            self._log("WARN", f"   ⚠ could not read the test report: {e}")

        if data is None:
            if repair and self._runner_gone(res.output):
                self._log("WARN", "   📦 the test runner is not installed — "
                                  "putting it back and running again")
                if self._reinstall_runner():
                    return self._run_locked(paths, repair=False,
                                            allow_batch=allow_batch,
                                            allow_fallback=allow_fallback)
            if paths and allow_fallback:
                self._log("WARN", "   ↪ targeted Vitest run produced no report — "
                                  "falling back to one complete-suite run")
                return self._run_locked(None, repair=repair, allow_batch=False,
                                        allow_fallback=False)
            tail = (res.output or "").strip().splitlines()[-3:]
            self._log("ERROR", "   ❌ QA infrastructure failure — Vitest produced "
                               "no JSON report")
            for line in tail:
                self._log("WARN", f"      {line[:150]}")
            return 0, [], False

        if self.collected_nothing(data):
            if repair and self._heal_environment(res.output):
                return self._run_locked(paths, repair=False,
                                        allow_batch=allow_batch,
                                        allow_fallback=allow_fallback)
            files = len(data.get("testResults") or [])
            self._log("ERROR", f"   ❌ QA infrastructure failure — Vitest "
                               f"collected {files} test file(s) and ran no "
                               f"cases, so nothing was verified")
            for line in (res.output or "").strip().splitlines()[-3:]:
                self._log("WARN", f"      {line[:150]}")
            return 0, [], False

        if paths:
            self._merge_into_report(data)

        passed, failures = self._parse(data)
        return passed, failures, True


