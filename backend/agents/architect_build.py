"""Task synchronization, build loop and generated-file flow."""
from .architect_common import *


class ArchitectBuildMixin:
    KIND_LABEL = {
        "server": "SERVER component — may be async and read the DB directly, "
                  "NO hooks, NO 'use client'",
        "client": "CLIENT component — 'use client' on line 1, hooks allowed, "
                  "fetch via /api, never touch @/lib/mongodb",
        "route":  "ROUTE HANDLER — named exports GET/POST/…, no default "
                  "export, no 'use client'",
    }

    _LOCAL_SPEC_RE = re.compile(
        r"""(?:from|import)\s+['"]@/((?:components|lib)/[\w./-]+)['"]""")

    def _unplanned_imports(self, rel: str) -> list:
        """Imports in `rel` that neither a file nor the PLAN will ever satisfy."""
        body = self.files.get(rel) or ""
        if not body:
            return []
        try:
            planned = {f["path"] for f in self._planned_files()}
        except Exception:
            return []

        out = []
        for spec in dict.fromkeys(self._LOCAL_SPEC_RE.findall(body)):
            cands = ([spec] if spec.endswith((".jsx", ".js", ".mjs"))
                     else [spec + ".jsx", spec + ".js", spec + "/index.jsx",
                           spec + "/index.js"])
            if any(c in self.files for c in cands):
                continue
            if any((self.project_dir / c).is_file() for c in cands):
                continue
            if any(c in planned for c in cands):
                continue  # Promised by the plan; a later turn writes it
            out.append((spec, f"imports '@/{spec}', which does not exist and is "
                              f"not in the plan, so no later task will create "
                              f"it \u2014 write that file in this same turn or "
                              f"import something that is already there"))
        return out

    def _planned_files(self) -> list:
        """Every file the plan promised, in plan order, de-duplicated."""
        out, seen = [], set()
        for ph in self.plan.get("phases", []):
            for f in ph.get("files", []):
                path = f["path"]
                if path in seen:
                    continue
                seen.add(path)
                out.append(f)
        return out

    def _acceptance_lines(self, f: dict) -> list:
        """The grep-level facts the analyzer will hold this file to."""
        path = str(f.get("path") or "")
        out = []
        for c in (self.plan or {}).get("contracts") or []:
            if not isinstance(c, dict):
                continue
            if str(c.get("from") or "") != path:
                continue
            target = str(c.get("target") or "").strip()
            if not target.startswith("/") or "[" in target:
                continue
            method = str(c.get("method") or "").upper()
            if c.get("kind") == "api" and method and method != "GET":
                out.append(f"fetch('{target}', {{ method: '{method}', … }}) "
                           f"— this exact URL string, in this file or a "
                           f"component it imports")
            else:
                out.append(f"fetch('{target}') — this exact URL string, in "
                           f"this file or a component it imports")
        for coll in f.get("reads") or []:
            api = f"/api/{str(coll).strip().lower()}"
            out.append(f"reads '{coll}': either getCollection('{coll}') here, "
                       f"or the literal fetch('{api}') here / in an imported "
                       f"component")
        for coll in f.get("writes") or []:
            api = f"/api/{str(coll).strip().lower()}"
            out.append(f"writes '{coll}': either getCollection('{coll}') "
                       f"here, or a non-GET fetch to the literal '{api}'")
        for row in self._file_actions(path):
            out.append(f"the control that does \"{row['phrase']}\" carries "
                       f"data-testid=\"{row['testid']}\" — that exact "
                       f"attribute, on the button/link itself")
        return out[:10]

    def _file_actions(self, path: str) -> list:
        """Workflow controls this file owns, from the shared id derivation."""
        try:
            from .action_ids import actions_for_file
            return actions_for_file(self.plan, path, self._planned_routes())
        except Exception as e:
            log.debug(f"file actions {path}: {e}")
            return []

    def _planned_routes(self) -> dict:
        """{route: {"file": …}} from the plan, before anything is on disk."""
        cached = getattr(self, "_planned_routes_cache", None)
        if cached is not None:
            return cached
        routes = {}
        try:
            for f in self._planned_files():
                rel = str(f.get("path") or "").replace("\\", "/")
                m = re.match(r"^app/(.*/)?page\.(?:jsx|js)$", rel)
                if not m:
                    continue
                seg = (m.group(1) or "").rstrip("/")
                seg = re.sub(r"\((?:[^)]*)\)/?", "", seg).strip("/")
                routes["/" + seg if seg else "/"] = {"file": rel}
        except Exception as e:
            log.debug(f"planned routes: {e}")
        self._planned_routes_cache = routes
        return routes

    def _action_id_block(self) -> str:
        """The one table that makes workflow controls findable by contract."""
        try:
            from .action_ids import workflow_actions
            rows = workflow_actions(self.plan)
        except Exception as e:
            log.debug(f"action id block: {e}")
            return ""
        if not rows:
            return ""
        lines = [
            "WORKFLOW CONTROL IDS — every control below MUST carry the exact",
            "`data-testid` given for it. The end-to-end run finds these by id,",
            "not by their wording, so you may write any copy you like; only the",
            "attribute is fixed. A missing one is reported as a build defect.",
        ]
        for r in rows[:24]:
            lines.append(f"  {r['route']:<22} {r['phrase'][:44]:<46} "
                         f"data-testid=\"{r['testid']}\"")
        lines.append("  Put it on the interactive element itself — the "
                     "<button>, <a> or <form>'s submit — never on a wrapper.")
        return "\n".join(lines)

    def _import_whitelist_block(self) -> str:
        """Every @/ path an import may name, existing or promised by the plan."""
        allowed = set()
        for rel in self.files:
            if rel.startswith(("components/", "lib/")):
                allowed.add("@/" + re.sub(r"\.(?:jsx|js|mjs)$", "", rel))
        try:
            for f in self._planned_files():
                rel = str(f.get("path") or "")
                if rel.startswith(("components/", "lib/")):
                    allowed.add("@/" + re.sub(r"\.(?:jsx|js|mjs)$", "", rel))
        except Exception:
            pass
        if not allowed:
            return ""
        return ("IMPORTABLE LOCAL MODULES — the complete list. An import of "
                "any other @/ path is a guaranteed build break, because "
                "nothing will ever create it:\n"
                + "\n".join(f"  {p}" for p in sorted(allowed)))

    def _file_list_block(self, files: list) -> str:
        """Each file with its whole brief: purpose, then every section."""
        out = []
        for f in files:
            kind = self.KIND_LABEL.get(f.get("kind", "server"), self.KIND_LABEL["server"])
            block = f"  • {f['path']}  [{kind}]"
            if f.get("purpose"):
                block += f"\n      {f['purpose']}"
            if f.get("reads"):
                block += "\n      reads Mongo collections: " + ", ".join(f["reads"])
            if f.get("writes"):
                block += "\n      writes Mongo collections: " + ", ".join(f["writes"])
            if f.get("sections"):
                block += "\n      on the page, top to bottom:"
                block += "".join(f"\n        - {s}" for s in f["sections"])
            if f.get("actions"):
                block += "\n      it must do:"
                block += "".join(f"\n        - {a}" for a in f["actions"])
            if f.get("invariants"):
                block += "\n      first-write invariants — all must be true before this file closes:"
                block += "".join(f"\n        - {x}" for x in f["invariants"])
            proofs = self._acceptance_lines(f)
            if proofs:
                block += ("\n      machine-checked after build — write these "
                          "literally:")
                block += "".join(f"\n        - {p}" for p in proofs)
            base = f["path"].rsplit("/", 1)[-1]
            if (not f.get("sections") and base.startswith("page.")
                    and not re.search(r"/(login|signup|register)/page\.",
                                      f["path"])):
                block += ("\n      the plan left this one thin — decide the "
                          "rest before you write it: at least three blocks "
                          "down the screen, each with the real data it is "
                          "about; a designed empty state carrying the action "
                          "that fills it; and every action this page's role "
                          "actually performs. Link only to paths the plan "
                          "lists, and add no navigation of your own beyond "
                          "the navbar every page renders.")
            out.append(block)
        return "\n".join(out)

    def _task_list_block(self, tasks: list) -> str:
        """The plan as numbered tasks — the shape the build is driven in."""
        out = []
        for i, t in enumerate(tasks, start=1):
            head = f"### Task {i} — {t.get('title', f'Task {i}')}"
            if t.get("goal"):
                head += f"\nGoal: {t['goal']}"
            if t.get("done_when"):
                head += f"\nDone when: {t['done_when']}"

            if t.get("covers"):
                head += f"\nCovers: {', '.join(t['covers'])}"
            block = head + "\n" + self._file_list_block(t.get("files", []))
            caps = self._capability_ledger(t.get("files", []))
            if caps:
                block += "\nCapabilities this task must prove, not merely render:\n" + caps
            handoffs = self._contract_ledger(t.get("files", []))
            if handoffs:
                block += ("\nCross-file contracts for this task — both ends must "
                          "agree exactly:\n" + handoffs)
            out.append(block)
        return "\n\n".join(out)

    def _current_task(self):
        """`(number, task, files still missing)` for the first unfinished task."""
        for i, t in enumerate(self.plan.get("phases", []), start=1):
            left = [f for f in t.get("files", []) if not self._on_disk(f["path"])]
            if left:
                return i, t, left
        return 0, None, []

    def _canonical_existing_path(self, path: str) -> str:
        """Return the written path that satisfies a plan entry, if any."""
        rel = str(path or "").lstrip("./").replace("\\", "/")
        if rel in self.files:
            return rel
        stem = re.sub(r"\.jsx?$", "", rel)
        for got in self.files:
            if re.sub(r"\.jsx?$", "", got) == stem:
                return got
        return ""

    def _is_scaffold_only(self, path: str) -> bool:
        """True when a planned file is still byte-for-byte temporary scaffold."""
        got = self._canonical_existing_path(path)
        if not got:
            return False
        baseline = (getattr(self, "_scaffold_baseline", {}) or {}).get(got)
        if baseline is not None and str(self.files.get(got, "")).strip() == str(baseline).strip():
            return True
        body = str(self.files.get(got, "") or "")
        return bool(got in {"app/page.jsx", "app/page.js"} and
                    re.search(r">\s*Building(?:…|\.\.\.)?\s*<", body, re.I))

    def _on_disk(self, path: str) -> bool:
        """Has this planned path been implemented, not merely scaffolded?"""
        got = self._canonical_existing_path(path)
        if not got:
            return False
        if got in set(getattr(self, "_invalid_generated", set()) or set()):
            return False
        return not self._is_scaffold_only(got)

    def _outstanding(self) -> list:
        """Planned file entries not yet on disk, still in plan order."""
        return [f for f in self._planned_files() if not self._on_disk(f["path"])]

    def _sync_phases(self, seen: dict, final: bool = False) -> None:
        """Keep the phase timeline in step with what is actually on disk."""
        phases = self.plan.get("phases", [])
        total = len(phases)
        announced_active = False

        for i, ph in enumerate(phases, start=1):
            if seen.get(i) == "done":
                continue
            title = ph.get("title", f"Phase {i}")
            want = [f["path"] for f in ph.get("files", [])]
            have = [p for p in want if self._on_disk(p)]

            if len(have) < len(want) and not final:

                if not announced_active:
                    announced_active = True
                    if seen.get(i) != "active":
                        self._fire("on_phase", {
                            "phase": i, "total": total, "title": title,
                            "status": "active", "goal": ph.get("goal", ""),
                            "files": want})
                        seen[i] = "active"
                continue

            if len(have) < len(want):
                self._log("WARN", f"   ⚠ Never written: "
                                  f"{', '.join(p for p in want if p not in have)}")

            if seen.get(i) != "active" or have != want:
                self._fire("on_phase", {
                    "phase": i, "total": total, "title": title,
                    "status": "active", "goal": ph.get("goal", ""),
                    "files": have})
            self._fire("on_phase", {"phase": i, "total": total, "title": title,
                                    "status": "done", "written": len(have)})
            seen[i] = "done"

    def build_app(self) -> int:
        """Write the whole app in one flowing conversation, one task per turn."""
        planned = self._planned_files()
        total_files = len(planned)
        if not planned:
            self._log("WARN", "   ⚠ Plan named no files — nothing to build")
            return 0

        extras = ""
        if self.plan.get("demo_accounts"):
            accounts = ", ".join(f"{a['email']} / {a['password']} ({a['role']})"
                                 for a in self.plan["demo_accounts"])
            extras += (f"\n\nDemo accounts (seed these EXACT values, hashed "
                       f"with bcrypt.hashSync. Do NOT display them anywhere "
                       f"in the UI): {accounts}")

        tasks = self.plan.get("phases", [])

        already = total_files - len(self._outstanding())
        resuming = already > 0
        if resuming:
            self._log("INFO", f"⚙️  Continuing — {already}/{total_files} files "
                              f"already on disk, {total_files - already} to write")
        else:
            self._log("INFO", f"⚙️  Building {total_files} files across "
                              f"{len(tasks)} tasks")

        opening = (textwrap.dedent("""\
            ## Finishing a build that was interrupted

            Most of this app is already written and on disk. Do not touch it,
            and do not write any file that is not listed below — what follows
            is only what is still missing, grouped by task. Match what is
            already there: keep the same naming, styling and data shapes.
            """) if resuming else textwrap.dedent("""\
            ## The build, broken into tasks

            The whole app is below, grouped into tasks so you can see which
            files belong together. Work straight down it, task by task, and do
            not stop between tasks — when you run out of room I will say
            "Continue" and you carry on where you left off.
            """))
        user = (opening
            + "\n" + (self._outstanding_by_task() if resuming
                      else self._task_list_block(tasks)) + extras + "\n"
            + ("\n" + self._import_whitelist_block() + "\n"
               if self._import_whitelist_block() else "")
            + ("\n" + self._action_id_block() + "\n"
               if self._action_id_block() else "")
            + textwrap.dedent("""\

            How this works:
              • One <write_file path="…">…</write_file> block per file. Real,
                finished code — no TODOs, no placeholders, no "rest of the
                code unchanged".
              • Take the tasks in order and keep going. Never rewrite a file
                you have already written.
              • The `purpose` line of each file says who the page is for.
                Write the guard it names and no other. A page for everyone
                gets no session check anywhere in the file — measured across
                fifteen generated apps, five sent `/` to `/login`, one of
                them against its own plan. A page for a role keeps the two
                checks apart, `if (!user) redirect('/login')` and then
                `if (user.role !== '…') redirect('/')`, and every page inside
                that section repeats both — the landing page having them says
                nothing about the pages under it.
              • Every file gets the full attention of its own task. A page
                that renders a heading and an empty div is not a finished
                page: it needs the real data — every independent collection
                read together in one `Promise.all`, not one await after
                another — the actions the feature promises, an empty state
                that carries the button which fills it, and a layout that
                holds together on a phone. A server page awaits its data
                before it renders, so it has no spinner of its own to show:
                the loading state is `app/loading.jsx`, which the plan lists
                as its own file.
              • Hold the design bar on every screen — the plan's one accent
                colour, the spacing rhythm, the type scale, hover and focus
                states on everything clickable, and a designed empty state.
                Ultra beautiful is the target, and restraint is how it is
                reached.
              • Stay consistent: what you write early is what the later tasks
                import, so keep the naming, props, styling and data shapes
                identical throughout.
              • Do not narrate between files and do not ask questions.
              • If you run out of room, stop at a file boundary — I will say
                "Continue" and you pick up at the next unwritten file.
              • When every task is done, reply exactly: BUILD COMPLETE

            Start now.
            """))

        max_turns = max(6, min(40, total_files + 6))
        seen, written_total, stalls = {}, 0, 0

        for turn in range(1, max_turns + 1):
            _wh = len(getattr(self, "_write_history", []))
            written = self._run_write_loop(user)
            written_total += written

            recent = list(dict.fromkeys(getattr(self, "_write_history", [])[_wh:]))
            turn_errors = []
            if recent:
                try:
                    all_errors = self.lint_generated()
                    local_hard = re.compile(
                        r"not valid JavaScript|contains TypeScript syntax|"
                        r"use client.*first line|imports react-router-dom|"
                        r"constructs a MongoClient|opens a MongoDB transaction|"
                        r"decrements .* with no guard|uses next/head|"
                        r"route handlers must use named exports|"
                        r"TypeScript files are banned|Pages Router is banned|"
                        r"has on[A-Z].*Server Component|"
                        r"renders .* but never imports or defines|"
                        r"uses runtime helper .* without importing or declaring|"
                        r"uses collection handle .* without declaring it|"
                        r"opened on line .* closed by", re.I)
                    turn_errors = [e for e in all_errors
                                   if any(str(e).startswith(rel + ":") for rel in recent)
                                   and local_hard.search(str(e))]

                    for rel in recent:
                        for _spec, why in self._unplanned_imports(rel):
                            turn_errors.append(f"{rel}: {why}")
                except Exception as e:
                    log.debug(f"per-turn generated-file gate: {e}")
            if turn_errors:
                bad = {rel for rel in recent
                       if any(str(e).startswith(rel + ":") for e in turn_errors)}
                self._invalid_generated |= bad
                self._log("WARN", f"   🧯 first-pass file gate — {len(bad)} newly written file(s) need correction before the next task")
                for e in turn_errors[:6]:
                    self._log("WARN", "      " + str(e)[:220])

            self._sync_phases(seen)
            self._fire("on_memory", self.memory_stats())

            self.save_convo()

            outstanding = self._outstanding()
            done_n = total_files - len(outstanding)
            index, _task, left = self._current_task()

            # Index only moves when every file of the previous task.
            was = getattr(self, "_task_seen", 0)
            if index != was:
                if was:
                    phases = self.plan.get("phases") or []
                    if 0 < was <= len(phases):
                        try:
                            self._finished_note = self._task_note(
                                was, phases[was - 1])
                        except Exception as e:
                            log.debug(f"task note: {e}")
                self._task_seen = index
            self._fire("on_progress",
                       (f"Task {index}/{len(tasks)}… {done_n}/{total_files} files"
                        if index else f"Writing files… {done_n}/{total_files}"),
                       int(18 + 60.0 * done_n / total_files))
            self._log("INFO", f"   📝 {done_n}/{total_files} files on disk "
                              f"(turn {turn}, +{written})")

            if not outstanding:
                break

            if written:
                stalls = 0
            else:
                stalls += 1
                if stalls >= 2:
                    self._log("WARN", "   ⚠ Two turns produced no files — "
                                      "handing what exists to the repair passes")
                    break

                self._log("WARN", "   ⚠ No files emitted — pushing harder")
                user = ("You produced no <write_file> blocks. Output ONLY "
                        "<write_file path=\"…\">…</write_file> blocks, starting "
                        "immediately with '<write_file', for these files:\n"
                        + self._file_list_block(left or outstanding))
                continue

            bad_now = [p for p in outstanding
                       if self._canonical_existing_path(p.get("path", "")) in
                          set(getattr(self, "_invalid_generated", set()) or set())]
            if bad_now and turn_errors:
                user = ("The files below were just written but failed the deterministic local gate. "
                        "Fix THESE files now before continuing to later tasks. Preserve their planned behavior and interfaces.\n\n"
                        + "\n".join("- " + e for e in turn_errors[:10])
                        + "\n\n" + self._import_whitelist_block()
                        + "\n\nOutput complete <write_file> blocks only for the failing files.")
            else:
                user = self._continue_prompt(outstanding)
        else:
            self._log("WARN", f"   ⚠ Stopped after {max_turns} turns with "
                              f"{len(self._outstanding())} file(s) unwritten")

        self._sync_phases(seen, final=True)
        return written_total
