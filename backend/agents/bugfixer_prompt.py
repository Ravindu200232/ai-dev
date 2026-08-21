"""Test weakening guards, neighboring context and concise repair prompt creation."""
from .bugfixer_common import *


class BugFixerPromptMixin:
    CASE_RE = re.compile(
        r"""\b(?:it|test)\s*(?:\.\s*\w+)?\s*\(\s*(['"`])(.+?)\1""", re.S)

    _CASE_START_RE = re.compile(
        r"""\b(?:it|test)(?:\s*\.\s*\w+)?\s*\(\s*(['"`])(?P<name>.+?)\1\s*,""",
        re.S)

    @classmethod
    def _case_blocks(cls, body: str):
        """[(name, start, end)] for every top-level it/test call, or None."""
        body = body or ""
        out = []
        for m in cls._CASE_START_RE.finditer(body):
            start = m.start()
            # The call's paren is the first one after `it`/`test` itself
            i = body.index("(", start)
            depth, n = 0, len(body)
            in_str, esc = "", False
            while i < n:
                ch = body[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == in_str:
                        in_str = ""
                    i += 1
                    continue
                if ch in "'\"`":
                    in_str = ch
                elif ch == "/" and i + 1 < n and body[i + 1] == "/":
                    i = body.find("\n", i)
                    if i < 0:
                        return None
                    continue
                elif ch == "/" and i + 1 < n and body[i + 1] == "*":
                    i = body.find("*/", i)
                    if i < 0:
                        return None
                    i += 2
                    continue
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        if end < n and body[end] == ";":
                            end += 1
                        out.append((m.group("name").strip(), start, end))
                        break
                i += 1
            else:
                return None
            if depth != 0 and (not out or out[-1][1] != start):
                return None
        return out

    @classmethod
    def _splice_passing(cls, old: str, new: str, failing) -> str:
        """`new` with every passing case's body taken verbatim from `old`."""
        failing = {str(f or "").strip() for f in (failing or [])}
        old_blocks = cls._case_blocks(old or "")
        new_blocks = cls._case_blocks(new or "")
        if not old_blocks or new_blocks is None:
            return ""
        old_by = {n: (s, e) for n, s, e in old_blocks}
        pieces, cursor = [], 0
        for name, s, e in new_blocks:
            if name in failing or name not in old_by:
                continue
            os_, oe = old_by[name]
            pieces.append(new[cursor:s])
            pieces.append(old[os_:oe])
            cursor = e
        if not pieces:
            return ""
        pieces.append(new[cursor:])
        return "".join(pieces)

    @classmethod
    def _lost_cases(cls, old, new, failing) -> str:
        """Passing cases this rewrite dropped, or `""`."""
        before = {m.group(2).strip() for m in cls.CASE_RE.finditer(old or "")}
        after = {m.group(2).strip() for m in cls.CASE_RE.finditer(new or "")}

        lost = (before - after) - {f.strip() for f in (failing or [])}
        if not lost:
            return ""
        shown = ", ".join(f'"{n[:44]}"' for n in sorted(lost)[:3])
        more = f" (+{len(lost) - 3} more)" if len(lost) > 3 else ""
        return (f"it drops {len(lost)} passing case(s) that nothing was wrong "
                f"with — {shown}{more}")

    @staticmethod
    def _weakened(old, new) -> str:
        """Why this rewrite makes the test worthless, or `""`."""
        if not new or not new.strip():
            return "the rewrite is empty"
        m = WEAKENED_RE.search(new)
        if m and not WEAKENED_RE.search(old or ""):
            return f"it introduces {m.group(0).strip()}"
        before = len(re.findall(r"\bexpect\s*\(", old or ""))
        after = len(re.findall(r"\bexpect\s*\(", new))
        if before and after * 2 < before:
            return f"the assertions went from {before} to {after}"
        if before and after == 0:
            return "every assertion was removed"
        return ""

    NEIGHBOUR_MAX = 5
    NEIGHBOUR_CHARS = 4000

    HARNESS_GLOBS = ("tests/helpers/*.js", "tests/setup.js", "vitest.config.js")

    def _harness_bodies(self) -> dict:
        """The harness files a test runs through, {path: body}."""
        out = {}
        for pat in self.HARNESS_GLOBS:
            try:
                for fp in sorted(self.project_dir.glob(pat)):
                    if not fp.is_file():
                        continue
                    rel = str(fp.relative_to(self.project_dir)).replace("\\", "/")
                    body = self._read(rel)
                    if body:
                        out[rel] = body
            except Exception as e:
                log.debug(f"harness bodies {pat}: {e}")
        return out

    def _neighbours(self, seen: dict) -> dict:
        """The local modules the test and its target import, {path: body}."""
        out = {}
        for rel, body in seen.items():
            if not rel or not body:
                continue
            for stmt in parse_imports(body):
                target = resolve_local(rel, stmt.spec, self.arch.files)
                if not target or target in seen or target in out:
                    continue
                if target.endswith(".css"):
                    continue
                out[target] = self.arch.files[target]
                if len(out) >= self.NEIGHBOUR_MAX:
                    return out
        return out

    def _prompt(self, failures, test_body, target_body, forced, reason,
                refused="", tier=0):
        f0 = failures[0]
        cases = []
        for f in failures[:6]:
            frames = [ln for ln in (f.stack or "").splitlines()
                      if ln.strip().startswith("at ")][:4]
            cases.append(f"  • {f.name}\n    {f.message}\n"
                         + ("\n".join(f"      {ln.strip()}" for ln in frames)))

        # Hide passing bodies when the splice can restore them: half
        shown_test = test_body
        failing_names = {str(f.name or "").strip() for f in failures}
        blocks = self._case_blocks(test_body) if len(test_body) > 2000 else None
        if blocks and failing_names and tier == 0:
            passing = [(n, s, e) for n, s, e in blocks
                       if n not in failing_names]
            if passing and any(n in failing_names for n, _, _ in blocks):
                pieces, cursor = [], 0
                for n, s, e in passing:
                    head = test_body.index("(", s)
                    comma = test_body.find(",", head)
                    if comma < 0 or comma > e:
                        pieces = None
                        break
                    pieces.append(test_body[cursor:comma + 1])
                    pieces.append(" () => { /* PASSING — hidden; the real "
                                  "body is restored after your write. "
                                  "Reproduce this stub exactly. */ })")
                    cursor = e
                if pieces is not None:
                    pieces.append(test_body[cursor:])
                    shown_test = "".join(pieces)
                    with self._lock:
                        self._collapsed.add(f0.test_file)

        parts = [f"## The failing test — {f0.test_file}\n```js\n{shown_test[:9000]}\n```"]
        if target_body:
            parts.append(f"## The file it is testing — {f0.target}\n"
                         f"```js\n{target_body[:9000]}\n```")
        else:
            parts.append("## The file it is testing\nThere is none — this "
                         "failure could not be attributed to any app file, so "
                         "only the test can be wrong.")

        ref = self._neighbours({f0.test_file: test_body, f0.target: target_body})
        if ref:
            parts.append("## What those files import — READ ONLY, do not write "
                         "them\nThis is how the project already does it. Use "
                         "these exports, hooks, props, and storage keys exactly "
                         "as they are written here. Do not invent a second way "
                         "to do something this already does.")
            for p, b in ref.items():
                parts.append(f"### {p} (reference)\n"
                             f"```js\n{b[:self.NEIGHBOUR_CHARS]}\n```")

        parts.append(f"## What Vitest reported ({len(failures)} case(s) failing)\n"
                     + "\n".join(cases))

        if forced:

            parts.append(f"## The verdict is already decided: {forced}\n"
                         f"{reason.capitalize()}. Do not argue with this — write "
                         f"the corrected {'test' if forced == 'test' else 'code'} "
                         f"file. Still emit the VERDICT line, with the evidence "
                         f"you would have given.")
        elif reason:

            parts.append(f"## What AgentForge noticed\n{reason.capitalize()}.\n\n"
                         f"That is an observation, not the answer. Weigh it "
                         f"against the two files and decide for yourself.")
        if f0.stale:
            parts.append("## Note\nThis test was written before its target was "
                         "last rewritten, so it may be describing code that no "
                         "longer exists.")

        if refused:
            parts.append(
                f"## Your last attempt at this file was THROWN AWAY\n"
                f"{refused.capitalize()}.\n\n"
                f"That write never reached disk, which is why you are seeing "
                f"the same failure again — it is not that your fix did not "
                f"work, it is that it was refused before it ran. Write "
                f"something that does not have that problem. If the only fix "
                f"you can see is the one that was refused, then the premise is "
                f"wrong: say so in the VERDICT line rather than writing it "
                f"again.")
        if tier >= 2 and target_body:
            parts.append(
                "## This is the third attempt on this file\n"
                "Two corrections of the TEST have already failed to make it "
                "pass. That is evidence about the component, not just the "
                "test: a case stays red like this when the component really "
                "is missing the role, label or behaviour the test looks for. "
                "You may write EITHER file now. Choose the one the evidence "
                "actually points at, and say which in the VERDICT line.")

        if tier >= 3:
            harness = self._harness_bodies()
            parts.append(
                "## Neither file may be the problem\n"
                "Rewriting the test failed. Rewriting the component failed. "
                "When both of those are true the remaining possibility is that "
                "the failure is not in this app at all — it is in the harness "
                "the test runs THROUGH, which is written by AgentForge and shown "
                "below.\n\n"
                "Real examples, both of which cost a whole stage:\n"
                "  • the request helper sent a JSON body to a handler that "
                "reads `request.formData()`, so the handler threw, its own "
                "`try/catch` returned 500, and the case failed as "
                "\"expected 500 to be 400\" — the route was correct\n"
                "  • a mock exported a different name than the module it "
                "stands in for, so every call came back undefined\n\n"
                "If that is what is happening here, answer "
                "`VERDICT :: harness :: <which file and what is wrong with "
                "it>` and write NOTHING. You cannot edit these files and you "
                "are not being asked to — naming it is the fix, and it is more "
                "useful than another rewrite of a file that is already right. "
                "If the harness is genuinely fine, ignore this section and "
                "answer as before.")
            for p, b in harness.items():
                parts.append(f"### {p} (AgentForge's, read only)\n"
                             f"```js\n{b[:4000]}\n```")

        parts.append("Emit the VERDICT line first, then exactly one "
                     "<write_file> block.")
        return "\n\n".join(parts)

    def summary(self) -> str:
        if not self.verdicts:
            return "no repairs attempted"
        code = sum(1 for v in self.verdicts if v.touched_code)
        tests = sum(1 for v in self.verdicts if v.written and not v.touched_code)
        held = sum(1 for v in self.verdicts if v.quarantine)
        return (f"{tests} test(s) corrected, {code} code fix(es), "
                f"{held} set aside")
