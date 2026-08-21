"""Deterministic unit-test quality guards for timers, forms, bodies and selectors."""
from .author_common import *


class UnitAuthorGuardMixin:
    NATIVE_EVENT_RE = re.compile(
        r"toBeInstanceOf\s*\(\s*(Event|MouseEvent|KeyboardEvent|SubmitEvent)\s*\)")

    SET_TIMEOUT_RE = re.compile(r"setTimeout\s*\([^,]+,\s*(\d+)\s*\)")
    WAIT_FOR_RE = re.compile(r"\bwaitFor\s*\(")
    TIMER_CONTROL_RE = re.compile(r"useFakeTimers|advanceTimersBy|runAllTimers"
                                  r"|timeout\s*:\s*\d+")

    CLICK_SUBMIT_RE = re.compile(
        r"(?:fireEvent\.click|user\.click|userEvent\.click)\s*\([^)]*"
        r"(?:submit|confirm|book|save|send|create|sign in|log in|add|update|"
        r"pay|place order)[^)]*\)", re.I)
    SUBMITS_PROPERLY_RE = re.compile(r"fireEvent\.submit\s*\(")

    BARE_FAKE_TIMERS_RE = re.compile(r"vi\s*\.\s*useFakeTimers\s*\(\s*\)")
    ADVANCING_TIMERS_RE = re.compile(
        r"useFakeTimers\s*\(\s*\{[^)]*shouldAdvanceTime")
    AWAITS_POLLING_RE = re.compile(
        r"await\s+(?:screen\s*\.\s*)?findBy[A-Z]\w*\s*\(|await\s+waitFor\s*\(")

    NEEDS_ROUTER_RE = re.compile(
        r"\buse(?:Router|Pathname|SearchParams|Params|SelectedLayoutSegment)\s*\(")
    MOCKS_NAV_RE = re.compile(r"""vi\.mock\s*\(\s*['"]next/navigation['"]""")

    def _unmocked_router(self, test_src: str, target_src: str) -> str:
        """A component that calls `useRouter`, in a test that never mocked."""
        if not self.NEEDS_ROUTER_RE.search(target_src or ""):
            return ""
        if self.MOCKS_NAV_RE.search(test_src or ""):
            return ""
        return ("the component calls a Next routing hook and this test does "
                "not mock next/navigation, so Next throws "
                "\"invariant expected app router to be mounted\" before "
                "anything renders. Add "
                "`vi.mock('next/navigation', () => import('../../helpers/navMock.js'))` "
                "at the top, and `__resetNav()` in beforeEach")

    _NOT_CODE_RE = re.compile(
        r"//[^\n]*"
        r"|/\*.*?\*/"
        r"|(?P<pre>[(,=:!&|?{;\[]\s*)"
        r"/(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n])+/[gimsuyd]*"
        r"|'(?:\\.|[^'\\\n])*'"
        r"|\"(?:\\.|[^\"\\\n])*\""
        r"|`(?:\\.|[^`\\])*`", re.S)

    @classmethod
    def _strip_not_code(cls, src: str) -> str:
        """Blank out what is not code, keeping every newline where it was so the line."""
        def blank(m):
            pre = m.group("pre") or ""
            return pre + "\n" * m.group(0)[len(pre):].count("\n")
        return cls._NOT_CODE_RE.sub(blank, src or "")
    _PAIRS = {")": "(", "]": "[", "}": "{"}

    def _unbalanced(self, test_src: str) -> str:
        """A bracket left open — the file cannot parse, so no case in it runs."""
        code = self._strip_not_code(test_src)
        stack, line = [], 1
        for ch in code:
            if ch == "\n":
                line += 1
            elif ch in "([{":
                stack.append((ch, line))
            elif ch in self._PAIRS:
                if not stack:
                    return (f"there is a stray `{ch}` on line {line} — the file "
                            f"cannot parse, so none of its cases run")
                want = self._PAIRS[ch]
                got, at = stack.pop()
                if got != want:
                    return (f"the `{got}` opened on line {at} is closed by `{ch}` "
                            f"on line {line} — the file cannot parse, so none "
                            f"of its cases run")
        if stack:
            got, at = stack[0]
            return (f"the `{got}` opened on line {at} is never closed — the "
                    f"file cannot parse, so none of its cases run. Add the "
                    f"missing `{ {'(': ')', '[': ']', '{': '}'}[got] }`.")
        return ""

    READS_FORM_RE = re.compile(r"\brequest\s*\.\s*formData\s*\(")
    READS_JSON_RE = re.compile(r"\brequest\s*\.\s*json\s*\(")
    SENDS_JSON_RE = re.compile(r"\b(?:postJson|patchJson|putJson|deleteJson)\s*\(")
    SENDS_FORM_RE = re.compile(r"\bpostForm\s*\(")

    def _wrong_body_kind(self, test_src: str, target_src: str) -> str:
        """A test that sends JSON to a handler that reads `request.formData()`."""
        if not self.READS_FORM_RE.search(target_src or ""):
            return ""

        if self.READS_JSON_RE.search(target_src or ""):
            return ""
        if not self.SENDS_JSON_RE.search(test_src or ""):
            return ""
        if self.SENDS_FORM_RE.search(test_src or ""):
            return ""
        return ("the handler reads `request.formData()` and this test sends a "
                "JSON body, so `formData()` throws, the handler's own catch "
                "returns 500 and every case fails as \"expected 500 to be …\". "
                "Use `postForm(HANDLER, { field: 'value' })` instead.")

    def _frozen_clock(self, test_src: str) -> str:
        """`vi.useFakeTimers()` in a file that then awaits `findBy…`/`waitFor`."""
        if not self.BARE_FAKE_TIMERS_RE.search(test_src or ""):
            return ""
        if self.ADVANCING_TIMERS_RE.search(test_src or ""):
            return ""
        if not self.AWAITS_POLLING_RE.search(test_src or ""):
            return ""
        return ("it calls `vi.useFakeTimers()` and then awaits `findBy…` or "
                "`waitFor`, which poll the clock the fake timers froze — the "
                "case will hang until the 10s timeout and die as "
                "STACK_TRACE_ERROR. Use "
                "`vi.useFakeTimers({ shouldAdvanceTime: true })`, or drop the "
                "fake timers and pass `{ timeout: 2500 }` to waitFor")

    def _clicks_instead_of_submitting(self, test_src: str,
                                      target_src: str) -> str:
        """A submit flow driven by a click, which in jsdom does nothing."""
        if "<form" not in (target_src or ""):
            return ""
        if "onSubmit" not in (target_src or ""):
            return ""
        if self.SUBMITS_PROPERLY_RE.search(test_src):
            return ""
        if not self.CLICK_SUBMIT_RE.search(test_src):
            return ""
        if "waitFor" not in test_src and "findBy" not in test_src:
            return ""
        return ("it submits by clicking the button, which does nothing in "
                "jsdom — the form's onSubmit never runs. Fill the fields, then "
                "`fireEvent.submit(container.querySelector('form'))`")

    def _bad_async_assumptions(self, test_src: str, target_src: str) -> str:
        """Assertions about React or about time that cannot hold."""
        m = self.NATIVE_EVENT_RE.search(test_src)
        if m:
            return (f"it asserts toBeInstanceOf({m.group(1)}) — React passes a "
                    f"SyntheticBaseEvent to handlers, which is never an "
                    f"instance of {m.group(1)}. Assert what the handler did "
                    f"with the event instead")

        delays = [int(d) for d in self.SET_TIMEOUT_RE.findall(target_src or "")]
        slow = [d for d in delays if d >= 1000]
        if (slow and self.WAIT_FOR_RE.search(test_src)
                and not self.TIMER_CONTROL_RE.search(test_src)):
            return (f"the component defers work by {max(slow)}ms "
                    f"(setTimeout), but waitFor gives up after 1000ms. Use "
                    f"vi.useFakeTimers() and advance the clock, or pass "
                    f"{{ timeout: {max(slow) + 500} }} to waitFor")
        return ""

    STYLE_ASSERT_RE = re.compile(r"\.(toHaveClass|toHaveStyle)\s*\(")

    def _asserts_styling(self, test_src: str) -> str:
        """Styling assertions, which are forbidden and cannot be made to hold."""
        hits = {m.group(1) for m in self.STYLE_ASSERT_RE.finditer(test_src)}
        if not hits:
            return ""

        return (f"it asserts styling with {', '.join(sorted(hits))} — classes "
                f"and inline styles are not the contract")

    _TESTID_ATTR_RE = re.compile(r"""data-testid\s*=\s*['\"]([\w:-]+)['\"]""")

    def source_closure(self, target_rel: str) -> str:
        """A component's source and the local children it renders."""
        body = (self.qa.read_source(target_rel) or "") if self.qa else ""
        if not body:
            return ""
        try:
            from agents.exports import parse_imports, resolve_local
            files = dict(getattr(self.arch, "files", None) or {})
            files.setdefault(target_rel, body)
            seen, queue, parts = set(), [(target_rel, 0)], [body]
            while queue:
                rel, depth = queue.pop(0)
                if rel in seen or depth >= 2:
                    continue
                seen.add(rel)
                src = files.get(rel)
                if src is None and self.qa:
                    src = self.qa.read_source(rel)
                    if src:
                        files[rel] = src
                if not src:
                    continue
                for stmt in parse_imports(src):
                    child = resolve_local(rel, stmt.spec, files)
                    if not child or child in seen:
                        continue
                    child_src = files.get(child) or (self.qa.read_source(child) if self.qa else "")
                    if child_src:
                        files[child] = child_src
                        parts.append(child_src)
                        queue.append((child, depth + 1))
            return "\n".join(parts)
        except Exception:
            return body

    def testids_for_target(self, target_rel: str, limit: int = 40) -> list:
        """Every `data-testid` this component and its children really set."""
        out = []
        for name in self._TESTID_ATTR_RE.findall(self.source_closure(target_rel)):
            if name not in out:
                out.append(name)
                if len(out) >= limit:
                    break
        return out

    def _invented_selectors(self, test_src: str, target_rel: str) -> str:
        """Selectors this test looks for that the component does not contain."""
        body = self.source_closure(target_rel)
        if not body:
            # "Could not read the target" is not "nothing to check".
            queries = (self.TESTID_Q_RE.search(test_src)
                       or self.ROLE_Q_RE.search(test_src))
            if queries:
                return (f"its selectors cannot be verified -- {target_rel} "
                        f"could not be read")
            return ""

        body += "\n" + "\n".join(self._mock_bodies(test_src))

        missing = []
        queried = [tid for _, tid in self.TESTID_Q_RE.findall(test_src)]
        queried += self.TESTID_CSS_RE.findall(test_src)
        for tid in dict.fromkeys(queried):
            if f'data-testid="{tid}"' not in body and f"data-testid='{tid}'" not in body:
                missing.append(f'testid "{tid}"')
        for expr in self.TESTID_DYNAMIC_RE.findall(test_src):
            missing.append(f'testid from `{expr.strip()[:40]}`, which is not a '
                           f'literal and cannot be checked against the source')

        low_body = body.lower()
        for kind, _, text in self.TEXTATTR_Q_RE.findall(test_src):

            attrs = self.TEXTATTR_SOURCES[kind]
            if not any(a in low_body for a in attrs):
                missing.append(f'{kind} "{text}" (no {attrs[0]} in the source)')
            elif text.lower() not in low_body:
                missing.append(f'{kind} "{text}"')
        low = body.lower()
        for _, role in self.ROLE_Q_RE.findall(test_src):
            r = role.strip().lower()
            if f'role="{r}"' in low or f"role='{r}'" in low:
                continue
            tags = self.IMPLICIT_ROLES.get(r)
            if tags is None:
                missing.append(f'role "{role}"')
            elif tags and not any(t in low for t in tags):

                missing.append(f'role "{role}" (no {tags[0]}… in the source)')
        if not missing:
            return ""
        uniq = list(dict.fromkeys(missing))
        return (f"it queries {', '.join(uniq[:3])} — "
                f"{'that is' if len(uniq) == 1 else 'those are'} not in "
                f"{target_rel}")

    SUSPECT_RE = re.compile(r"//\s*SUSPECT:\s*(.+)")

    def _suspects(self, written):
        """Surface `// SUSPECT:` notes as findings for a human, not as failures."""
        if not self.qa:
            return
        for rel in written:
            body = self.qa.read_source(rel) or ""
            for m in self.SUSPECT_RE.finditer(body):
                note = m.group(1).strip()[:160]
                self.qa.report.suspects.append({"test": rel, "note": note})
                self._log("WARN", f"   🔎 {rel}: {note}")
