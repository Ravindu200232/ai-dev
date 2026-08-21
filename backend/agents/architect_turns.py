"""Continuation prompts, turn reminders, write loop and import parsing."""
from .architect_common import *


class ArchitectTurnMixin:
    def _task_now(self) -> str:
        """The task this turn is on, named, with the line that finishes it."""
        i, task, left = self._current_task()
        if not i or not task:
            return ""
        total = len(self.plan.get("phases") or [])
        out = [f"You are on Task {i} of {total} — "
               f"{task.get('title', f'Task {i}')}."]
        if task.get("goal"):
            out.append(f"Goal: {task['goal']}")
        if task.get("done_when"):
            out.append(f"Done when: {task['done_when']}")
        out.append(f"{len(left)} of its file(s) are still missing. Finish them "
                   f"before you start the next task — and if you have room "
                   f"afterwards, carry straight on into it rather than stopping.")
        return "\n".join(out)

    def _continue_prompt(self, outstanding: list) -> str:
        """The "keep going" turn: what is left, and nothing else to re-read."""
        parts = ["Continue."]

        # Which task, before anything else.
        here = self._task_now()
        if here:
            parts.append(here)

        # A task boundary just went past.
        note = getattr(self, "_finished_note", "")
        if note:
            parts.append(note)
            self._finished_note = ""

        if self._last_truncated:
            parts.append(f"`{self._last_truncated}` was cut off mid-file. "
                         f"Write it again in full first, then carry on.")

        # Every turn, not only the tight ones.
        ledger = self._symbol_ledger()
        if ledger:
            parts.append(ledger)

        # The palette, the roles and the routes.
        house = self._house_ledger()
        if house:
            parts.append(house)

        # Attached when the transcript has stopped holding the source.
        if self._bodies_stubbed():
            caps = self._room_for_snapshot(sum(len(p) for p in parts))
            if caps:
                parts.append(f"{SNAPSHOT_OPEN}\n"
                             + self._context_snapshot(wanted=outstanding, **caps)
                             + f"\n{SNAPSHOT_CLOSE}")

        grouped = self._outstanding_by_task()
        parts.append("Still to write:\n\n" + (grouped
                     or self._file_list_block(outstanding)))
        parts.append("Before closing each file, satisfy every machine-checked "
                     "line in its brief on THIS write: reads/writes, contract "
                     "URL+method, and exact action data-testid. With auth, root "
                     "app/layout stays structural and never renders Navbar. "
                     "Do not defer any of these to QA.")
        parts.append("Keep going until they all exist. Complete files, one "
                     "<write_file> block each, no narration. Finished pages "
                     "with real data — every independent read in one "
                     "Promise.all — every section the brief above names, an "
                     "empty state carrying the button that fills it, and a "
                     "failure the reader can act on. That brief also says who "
                     "each page is for, and that is the guard you write: a "
                     "page for one role checks the role in ITS OWN file, the "
                     "third page of a section as much as its landing page, "
                     "because Next inherits nothing — and a page whose brief "
                     "says PUBLIC gets no session check at all. Every page "
                     "except login and signup renders <Navbar /> itself at "
                     "the top of what it returns, and a page with pages under "
                     "it carries the link to each one. Hold the design "
                     "exactly: the same accent, the same spacing rhythm, a "
                     "layout that works at 375px, and all four states written "
                     "out on every button, link and input — rest, `hover:`, "
                     "`focus-visible:ring-2 focus-visible:ring-offset-2`, and "
                     "`disabled:opacity-50 disabled:cursor-not-allowed` "
                     "wherever the element can be disabled.")

        # Last, after the fixed paragraph above.
        nudge = self._turn_reminder(outstanding[:6])
        if nudge:
            parts.append(nudge)
        return "\n\n".join(parts)

    # These five are QUOTES.
    TURN_REMINDERS = {
        "thin":
            '"The last ones — the manager\'s screens, the edit that the '
            'create implied, the page at the end of a journey — are exactly '
            'as required as the first, and they are the ones that get thin '
            'when the build is tired." You are in that last third now. '
            'Measured across the 15 finished apps: the median file written '
            'there is 71 lines against 86 in the first third, and 39 of those '
            '126 come in under 40 lines against 26 of 133.',
        "states":
            '"EVERY INTERACTIVE ELEMENT HAS FOUR STATES, always written out: '
            'rest, `hover:`, `focus-visible:ring-2 focus-visible:ring-offset-2`'
            ', and `disabled:opacity-50 disabled:cursor-not-allowed`." '
            'Measured across the 15 finished apps: 147 files render a '
            '`<button>`; `transition-` is in every one of them and '
            '`focus-visible:` is in 9 — and of the 40 such files written in '
            'the last third of a build, none. Same bullet, one clause kept. '
            'Write all four on every button, link and input in these files.',
        "catch":
            '"`console.error(err)` alone is not error handling — the console '
            'is not on the screen." Measured across the 15 finished apps: of '
            'the 43 client files that fetch and catch, 22 leave the failure '
            'somewhere nobody can read it. Every catch you write this turn '
            'ends by setting an error state, and the component renders that '
            'state where the action was.',
        "label":
            "\"Every form input needs BOTH a `placeholder` and a label tied "
            "to it: `<label htmlFor=\"email\">` with `<input id=\"email\" …>`.\" "
            "The 51-of-57 in that rule counts labelled forms; this counts "
            "whole files. Measured across the 15 finished apps: 17 of the 66 "
            "files containing an `<input>` have no `htmlFor` anywhere in them "
            "at all. A bare <label> sitting next to an <input> associates "
            "nothing.",
        "empty":
            '"THE EMPTY STATE IS A DESIGNED SCREEN, not a fallback string. A '
            'list with nothing in it gets a centred block: a muted icon, one '
            'line saying what goes here, and the button that creates the '
            'first one." Measured across the 15 finished apps: 77 of the 154 '
            'files that map over a collection have no zero-length branch at '
            'all. Write the branch in the file that renders the list — and if '
            'the plan named a shared empty-state component, import that one '
            'rather than inventing a second.',
    }

    # Anchored both ends, and deliberately narrow.
    FORM_RE = re.compile(r"\b(forms?|inputs?|fields?|textareas?|validation|"
                         r"search|filters?|sign ?ups?|sign ?in|log ?in|"
                         r"register|uploads?)\b")
    # Any plural noun in a file's purpose means it renders many rows, so the
    # shape is read off the grammar rather than off a list of business nouns.
    LIST_RE = re.compile(r"\b(lists?|tables?|grids?|rows?|feeds?|catalogues?|"
                         r"catalogs?|history|results|inventory|"
                         r"[a-z]{4,}(?<!ss)(?<!us)(?<!is)s)\b")

    def _turn_reminder(self, files: list) -> str:
        """One rule from the system prompt, re-sent with the turn that is about to break."""
        if not files:
            return ""
        kinds = {f.get("kind", "server") for f in files}
        if kinds == {"route"}:
            return ""

        paths = " ".join(f.get("path", "") for f in files)
        brief = " ".join([f.get("purpose") or "" for f in files]
                         + [s for f in files for s in (f.get("sections") or [])]
                         + [a for f in files for a in (f.get("actions") or [])]
                         ).lower()

        planned = len(self._planned_files())
        written = planned - len(self._outstanding())

        keys = []
        if planned and written * 3 > planned * 2:
            keys.append("thin")
        if "client" in kinds or "components/" in paths:
            keys.extend(("states", "catch"))
        if self.FORM_RE.search(brief):
            keys.append("label")
        if self.LIST_RE.search(brief):
            keys.append("empty")
        if not keys:
            return ""

        sent = getattr(self, "_reminders_sent", set())
        fresh = [k for k in keys if k not in sent]
        if not fresh:
            sent, fresh = set(), keys
        sent.add(fresh[0])
        self._reminders_sent = sent
        return ("Still in force, quoted back from the rules at the top of "
                "this thread:\n" + self.TURN_REMINDERS[fresh[0]])
    def _run_write_loop(self, user_content: str, _tool_depth: int = 0) -> int:
        """Continue the running conversation with one more turn."""

        if _tool_depth == 0:
            self._workspace_tool_cache = {}
        self.convo.append({"role": "user",
                           "content": user_content + self._refusal_note()})
        self._refused = {}
        self._trim_convo()
        self._last_truncated = None

        state = {"count": 0, "path": None, "buf": []}
        raw = []

        def on_text(t):
            t = t.strip()

            if t and len(t) > 2 and t.upper().strip(".!* ") != "BUILD COMPLETE":
                self._fire("on_chat", t)

        def on_start(path):
            state["path"] = path
            state["buf"] = []
            self._fire("on_file_start", path)

        def on_token(tok):
            state["buf"].append(tok)
            self._fire("on_file_token", state["path"], tok)

        def on_end(path, content):
            self._fire("on_file_end", path, content)
            if self.write_file(path, content):
                state["count"] += 1
            elif self._stuck_on_refusals():

                raise _RefusalLoop()
            state["path"] = None

        parser = FileStreamParser(on_text, on_start, on_token, on_end)

        def feed(delta):
            raw.append(delta)
            parser.feed(delta)

        def close_parser():

            if parser.mode == "file":
                self._last_truncated = parser.path
            parser.close()

        try:
            tool_calls = self._stream(self.convo, feed, temperature=0.5)
        except _RefusalLoop:

            self._log("WARN", "   ⏹ stopped this turn — it was re-writing "
                              "files that had already been refused")
            close_parser()
            self.convo.append({"role": "assistant", "content": "".join(raw)})
            return state["count"]
        except Exception as e:
            self._log("ERROR", f"   ❌ Generation failed: {e}")
            close_parser()
            self.convo.append({"role": "assistant", "content": "".join(raw)})
            return state["count"]

        close_parser()
        reply = "".join(raw)

        self.convo.append({"role": "assistant", "content": reply})

        if self.stack == "next":
            docs = docsindex.serve(self.project_dir, reply)
            if docs:
                asked = docsindex.READ_RE.findall(reply)
                self._log("INFO", f"   📖 read_docs: {', '.join(asked[:3])}")
                self.convo.append({
                    "role": "user",
                    "content": docs + "\n\nThat is the documentation for the "
                                      "version installed here. Continue.",
                })

        results = self.run_requested_commands(reply)
        if results:
            self.convo.append({
                "role": "user",
                "content": "Command output:\n\n" + "\n\n".join(results)
                           + "\n\nContinue. Do not repeat a command that "
                             "already succeeded.",
            })

        for tc in tool_calls:
            fn = (tc or {}).get("function") or {}
            if fn.get("name") != "write_file":
                continue
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            path, content = args.get("path"), args.get("content")
            if path and content and path not in self.files:
                self._fire("on_file_start", path)
                self._fire("on_file_end", path, content)
                if self.write_file(path, content):
                    state["count"] += 1

        # Continue with the next focused turn.
        if state["count"] == 0 and _tool_depth < 3:
            try:
                from .workspace import WorkspaceTools
                observations, used = WorkspaceTools(self).serve(reply)
            except Exception as e:  # noqa: BLE001
                observations, used = "", 0
                log.debug(f"workspace tool serving failed: {e}")
            if used:
                self._log("INFO", f"   🧰 builder inspected {used} workspace tool(s)")
                self.convo.append({
                    "role": "user",
                    "content": "Tool observations:\n\n" + observations +
                               "\n\nContinue the same task. Use the evidence; do not repeat a tool call.",
                })
                return self._run_write_loop(
                    "Continue from the tool observations above. Write only the files the current task requires.",
                    _tool_depth=_tool_depth + 1)

        return state["count"]

    LOCAL_IMPORT_RE = re.compile(
        r"""import\s+(?:\w+|\{[^}]*\}|\w+\s*,\s*\{[^}]*\})\s+from\s+['"](\.[^'"]+)['"]""")

    ALIAS_IMPORT_RE = re.compile(
        r"""import\s+(?:\w+|\{[^}]*\}|\w+\s*,\s*\{[^}]*\})\s+from\s+['"]@/([^'"]+)['"]""")

    @staticmethod
    def _normalise(path: str) -> str:
        parts = []
        for seg in path.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg not in (".", ""):
                parts.append(seg)
        return "/".join(parts)

    NAMED_IMPORT_RE = re.compile(
        r"""import\s+(?:\w+\s*,\s*)?\{([^}]*)\}\s+from\s+(['"])([^'"]+)\2""")

    DECL_EXPORT_RE = re.compile(
        r"""export\s+(?:async\s+)?(?:function|const|let|var|class)\s+(\w+)""")

    LIST_EXPORT_RE = re.compile(r"""export\s*\{([^}]*)\}""")

    def _named_exporters(self) -> dict:
        """Symbol -> the set of files that export it by that name."""
        out = {}
        for path, content in self.files.items():
            if not path.endswith((".js", ".jsx")):
                continue
            names = set(self.DECL_EXPORT_RE.findall(content))
            for group in self.LIST_EXPORT_RE.findall(content):
                for entry in group.split(","):
                    entry = entry.strip()
                    if not entry:
                        continue

                    names.add(re.split(r"\s+as\s+", entry)[-1].strip())
            for n in names:
                out.setdefault(n, set()).add(path)
        return out
