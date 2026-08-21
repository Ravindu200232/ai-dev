"""Unit/runtime fix execution and guarded commit logic."""
from .bugfixer_common import *


class BugFixerApplyMixin:
    def fix(self, failures, *, build_ok=True, round_no=1,
            tier=0) -> FixVerdict:
        """Arbitrate and repair one test file's failures."""
        if not failures:
            return FixVerdict(test_file="", verdict="test",
                              evidence="there is nothing to repair")
        f0 = failures[0]
        test_file = f0.test_file
        target = (f0.target or "").strip()
        v = FixVerdict(test_file=test_file, target=target,
                       failing=[f.name for f in failures if f.name])

        forced, reason, action = self.prior(failures, build_ok=build_ok,
                                            round_no=round_no)
        if forced:
            v.forced = reason
        if action == "quarantine":
            v.verdict, v.evidence, v.quarantine = "test", reason, True
            self._log("WARN", f"   🚧 {test_file} — {reason}")
            with self._lock:
                self.verdicts.append(v)
            return v
        if action == "report":

            v.verdict, v.evidence = forced, reason
            self._log("WARN", f"   ⚠ {test_file} — {reason}")
            with self._lock:
                self.verdicts.append(v)
            return v

        test_body = self._read(test_file) or ""
        target_body = self._read(target) if target else ""
        if not test_body.strip():
            v.verdict, v.evidence = "test", "the test file is gone"
            with self._lock:
                self.verdicts.append(v)
            return v

        if tier >= 2 and target:
            reason = (reason + " — and two corrections of the test did not "
                      "make it pass, so the component is the thing left to "
                      "doubt").strip(" —")
        allowed = {test_file, target} - {""}
        self.arch._workspace_tool_cache = {}
        convo = [{"role": "system", "content": SYSTEM + "\n\n" + TOOL_HELP},
                 {"role": "user", "content": self._prompt(
                     failures, test_body, target_body, forced, reason,
                     refused=self.refusals.get(test_file, "") if tier else "",
                     tier=tier)}]

        raw = []
        state = {"verdict": "", "evidence": ""}

        def read_verdict():
            m = VERDICT_RE.search("".join(raw))
            if m:
                state["verdict"] = m.group(1).lower()
                state["evidence"] = (m.group(2) or "").strip()[:200]
            return state["verdict"]

        def on_end(path, content):
            key = (path or "").strip().lstrip("./").replace("\\", "/")

            said = read_verdict() or "test"
            if forced:
                said = forced
            want = {test_file} if said != "code" else ({target} if target else set())
            if key not in want or key not in allowed:
                v.rejected.append(key)
                self._log("WARN", f"   ⛔ {key} is not writable under a "
                                  f"'{said}' verdict — skipped")
                return
            self._commit(key, content, said, v)

        parser = FileStreamParser(
            on_text=lambda t: raw.append(t),
            on_file_start=lambda p: self._fire("on_file_start", p),
            on_file_token=lambda t: None,
            on_file_end=on_end)
        try:
            for tool_turn in range(2):
                turn = []
                def feed(tok):
                    turn.append(tok)
                    parser.feed(tok)
                self.arch._stream(convo, feed, temperature=TEMPERATURE,
                                  model=self.model, timeout=CALL_BUDGET)
                reply = "".join(turn)
                convo.append({"role": "assistant", "content": reply})
                observations, used = WorkspaceTools(self.arch).serve(reply)
                if used and not v.written and tool_turn == 0:
                    self._log("INFO", f"   🧰 unit fixer inspected {used} workspace tool(s)")
                    convo.append({"role": "user", "content":
                                  "Tool observations:\n\n" + observations +
                                  "\n\nContinue the same failing-test diagnosis. Emit the verdict before any write."})
                    continue
                break
        except Exception as e:
            self._log("WARN", f"   ⚠ bug fixer failed on {test_file}: {e}")
        parser.close()

        said = read_verdict()
        v.verdict = forced or said or "test"
        v.evidence = state["evidence"] or reason
        if said == VERDICT_HARNESS:

            v.quarantine = True
            self._log("WARN", f"   🧰 {test_file} — the fixer says the fault is "
                              f"in AgentForge's own test harness, not this app:")
            self._log("WARN", f"      {v.evidence or 'no detail given'}")
            self._log("WARN", f"      Nothing was rewritten. This one needs a "
                              f"change to AgentForge itself.")
        elif said == "unclear" and not v.written:
            v.quarantine = True
            self._log("WARN", f"   🚧 {test_file} — the fixer could not tell: "
                              f"{v.evidence or 'no evidence given'}")
        elif not said:

            self._log("WARN", f"   ⚠ no verdict from the fixer on {test_file} "
                              f"— treated as a test problem")

        with self._lock:

            self.verdicts.append(v)
        return v

    def fix_runtime(self, errors: str, spec, *, server_log: str = "",
                    round_no: int = 1, privileged_paths=None) -> list:
        """Repair what broke when the app ran, against a plan of which files change."""

        planned = [f for f in getattr(spec, "files", [])
                   if self.runtime_editable(f["path"], privileged_paths)]
        if not planned:
            return []

        allowed = {f["path"] for f in planned}
        bodies = {f["path"]: (self._read(f["path"]) or "") for f in planned}
        written = []

        parts = [f"## What went wrong\n```\n{errors[:5000]}\n```"]
        if server_log.strip():
            parts.append("## The dev server's own output — the stack frames "
                         "name the file and line\n"
                         f"```\n{server_log[:3500]}\n```")
        if getattr(spec, "summary", ""):
            parts.append(f"## What the plan concluded\n{spec.summary}")

        reasoning = getattr(spec, "context", {}) or {}
        if isinstance(reasoning, dict):
            ev = reasoning.get("evidence") or []
            evidence_text = "\n".join(
                f"- {item.get('path')}: {item.get('fact')}"
                for item in ev if isinstance(item, dict) and item.get("path"))
            parts.append(
                "## Evidence-backed diagnosis\n"
                f"Current behavior: {reasoning.get('current') or '(not recorded)'}\n"
                f"Exact gap: {reasoning.get('gap') or '(not recorded)'}\n"
                f"Root cause / owner: {reasoning.get('cause') or '(not recorded)'}\n"
                f"Source evidence:\n{evidence_text or '- (none)'}\n"
                f"Required proof: {reasoning.get('verify') or '(not recorded)'}")

            # Older callers may still provide a dedicated references
            ref = reasoning.get("references") or {}
        else:
            ref = {}
        if isinstance(ref, dict) and ref:
            parts.append("## Read these — you may NOT write them\n"
                         "They are how this project already does it. Use the "
                         "exports, hooks, context and storage keys they define. "
                         "Do not reimplement what is here and do not invent a "
                         "second way to do the same thing.")
            for p, b in list(ref.items())[:6]:
                parts.append(f"### {p} (reference only)\n```js\n{str(b)[:6000]}\n```")

        parts.append("## Initial impact files, and why each one is suspected")
        for f in planned:
            body = bodies[f["path"]]
            why = f.get("why") or "named by the repair plan"
            if body:
                parts.append(f"### {f['path']} — {why}\n```js\n{body[:9000]}\n```")
            else:
                parts.append(f"### {f['path']} — {why}\n"
                             f"(this file does not exist yet; create it)")
        parts.append("Write the complete repair file set, one <write_file> block each. "
                     "You may expand beyond the initial list when workspace evidence proves another safe source file is required.")

        convo = [{"role": "system", "content": RUNTIME_SYSTEM + "\n\n" + TOOL_HELP},
                 {"role": "user", "content": "\n\n".join(parts)}]
        self.arch._workspace_tool_cache = {}

        def on_end(path, content):
            key = (path or "").strip().lstrip("./").replace("\\", "/")

            if not self.runtime_editable(key, privileged_paths):
                self._log("WARN", f"   ⛔ ignored unsafe repair path {key}")
                return
            if key not in allowed:
                self._log("INFO", f"   ↗ repair impact expanded to {key} after source inspection")
            old = bodies.get(key)
            if old is None:
                old = self._read(key) or ""
                bodies[key] = old
            if self._commit_runtime(key, content, old):
                written.append(key)

        raw = []
        parser = FileStreamParser(
            on_text=lambda t: raw.append(t),
            on_file_start=lambda p: self._fire("on_file_start", p),
            on_file_token=lambda t: None,
            on_file_end=on_end)
        old_priv = set(getattr(self.arch, "_e2e_privileged_paths", set()) or set())
        seen_observations = set()
        try:
            self.arch._e2e_privileged_paths = old_priv | set(privileged_paths or [])
            while True:
                turn = []
                def feed(tok):
                    turn.append(tok); parser.feed(tok)
                self.arch._stream(convo, feed, temperature=TEMPERATURE,
                                  model=self.model, timeout=CALL_BUDGET)
                reply = "".join(turn)
                convo.append({"role":"assistant", "content":reply})
                observations, used = WorkspaceTools(self.arch).serve(reply)
                if used and not written:
                    sig = observations.strip()
                    context_chars = sum(len(str(m.get("content", ""))) for m in convo)
                    budget_chars = 0
                    try:
                        budget_chars = int(self.arch._budget_chars())
                    except Exception:
                        try:
                            budget_chars = int(getattr(self.arch, "context_tokens", 0) or 0) * 3
                        except Exception:
                            budget_chars = 0
                    if sig and sig not in seen_observations and (not budget_chars or context_chars < budget_chars * 0.82):
                        seen_observations.add(sig)
                        self._log("INFO", f"   🧰 runtime fixer inspected {used} workspace tool(s)")
                        convo.append({"role":"user", "content":
                                      "Tool observations:\n\n" + observations +
                                      "\n\nContinue the SAME root-cause repair. Follow the evidence; expand the source file set if the dependency chain proves it is necessary. Do not repeat a tool call."})
                        continue
                    if sig in seen_observations:
                        self._log("WARN", "   ↔ runtime fixer repeated the same inspection — deciding from current evidence")
                break
        except Exception as e:
            self._log("WARN", f"   ⚠ runtime repair failed: {e}")
        finally:
            self.arch._e2e_privileged_paths = old_priv
        parser.close()

        if not written:
            self._log("WARN", f"   ⚠ Round {round_no} changed nothing")
            return written

        self.arch.repair_missing_imports()
        self.arch.sync_dependencies()
        return written

    def _commit_runtime(self, key, content, old) -> bool:
        """A runtime repair write."""
        if not content or not content.strip():
            self._log("WARN", f"   ⛔ {key}: the rewrite is empty")
            return False
        if not self.arch.write_file(key, content):
            return False
        self.app_writes.add(key)
        self._log("INFO", f"   🔧 {key} — rewritten")
        return True

    def _refuse(self, v, key, why: str) -> None:
        """Throw a write away, and REMEMBER WHY."""
        v.rejected.append(key)
        self._log("WARN", f"   ⛔ {key}: {why}")
        with self._lock:
            self.refusals[v.test_file or key] = why

    def _commit(self, key, content, said, v):
        """Write, after the guards that the verdict alone cannot enforce."""
        old = self._read(key) or ""

        if said == "code":
            if not self.editable(key):
                self._refuse(v, key, "that file is not editable")
                return

            bad = guard_scope(old, content, adding=True, retexting=True)
            if bad:
                self._refuse(v, key, bad)
                return
            if not self.arch.write_file(key, content):
                return
            self.app_writes.add(key)
            v.written = key
            self._log("INFO", f"   🔧 {key} — {v.evidence or 'fixed'}")
            return

        weak = self._weakened(old, content)
        if weak:
            self._refuse(v, key, f"{weak} — the test is not repaired, it is "
                                 f"disabled")
            return

        lost = self._lost_cases(old, content, v.failing)
        if lost:
            self._refuse(v, key, lost)
            return

        # Whatever was written, put every passing case back
        collapsed = key in getattr(self, "_collapsed", set())
        spliced = self._splice_passing(old, content, v.failing) if v.failing else ""
        if collapsed:
            with self._lock:
                self._collapsed.discard(key)
            if not spliced:
                self._refuse(v, key, "the hidden passing cases could not be "
                                     "restored into this rewrite — resend "
                                     "with the full file")
                return
        if spliced and spliced != content:
            if not self._weakened(old, spliced) and \
                    not self._lost_cases(old, spliced, v.failing):
                content = spliced
                self._log("INFO", f"   🧵 {key} — passing case(s) kept "
                                  "byte-for-byte from the previous file")
        if self.qa:
            meta = self.qa.manifest.get(key) or {}
            ok = self.qa.write_test_file(key, content,
                                         target=meta.get("target", v.target),
                                         phase=meta.get("phase", 0),
                                         tier=meta.get("tier", 0))
            if ok:

                if key in self.qa.manifest:
                    self.qa.manifest[key]["stale"] = False
                v.written = key
                self._log("INFO", f"   🧪 {key} — {v.evidence or 'test corrected'}")
