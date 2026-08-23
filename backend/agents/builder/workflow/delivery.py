"""Delivery-gap repair, resume, and package installation helpers."""
from agents.builder.orchestration.common import *


class ArchitectDeliveryMixin:
    def run(self, user_prompt: str, *, requirement_source: str = "") -> bool:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        planner_where = "☁️ cloud" if self.planner_is_cloud else "💻 local"
        design_where = "☁️ cloud" if self.design_is_cloud else "💻 local"
        builder_where = "☁️ cloud" if self.is_cloud else "💻 local"
        self._log("INFO", f"🧭 Planner — {self.planner_model} "
                          f"({planner_where}, ctx {self.planner_num_ctx:,}, thinking off)")
        self._log("INFO", f"🎨 Design — {self.design_model} "
                          f"({design_where}, ctx {self.design_num_ctx:,}, thinking off)")
        self._log("INFO", f"🔨 Builder — {self.model} "
                          f"({builder_where}, ctx {self.num_ctx:,}, "
                          f"thinking {'on' if self.think else 'off'})")

        try:
            self._fire("on_progress", "Planning…", 5)
            if not self.make_plan(user_prompt, requirement_source=requirement_source):
                return False

            self._fire("on_progress", "Scaffolding…", 15)
            self.scaffold()
            self.install_planned_deps()

            self._fire("on_progress", "Writing files…", 18)
            self.build_app()
            remaining = self.close_delivery_gaps(max_rounds=2)
            if remaining:
                # A gap the builder thread could not close is a repair job, not
                # a dead build. Aborting here threw away a nearly complete app
                # and every downstream pass that can still write the file — the
                # analyzer repairs exactly this (`report.missing`).
                self.delivery_gaps_left = list(remaining)
                self._log("WARN", "   ⚠ Delivery gaps the builder could not "
                                  "close on its own — handing them to the "
                                  "repair passes: " + "; ".join(remaining[:8]))
            else:
                self.delivery_gaps_left = []
            self._fire("on_memory", self.memory_stats())

            self._fire("on_progress", "Checking imports…", 80)
            self.repair_missing_imports()
            self.apply_next_fixes()
            self.sync_dependencies()
            self.install_unresolved()
            self.repair_lint()

            # Before the verdict, not after.
            self.restore_owned()

            return self._verify_output()
        finally:

            self.save_convo()

    def delivery_gaps(self) -> list:
        """Deterministic post-build closure gate for pages, capabilities and flows."""
        gaps = []
        for rel in self.missing_planned_files():
            if self._is_scaffold_only(rel):
                gaps.append(f"planned file is still scaffold placeholder: {rel}")
            else:
                gaps.append(f"missing planned file: {rel}")

        caps = [c for c in (self.plan.get("capabilities") or []) if isinstance(c, dict)]
        workflows = [w for w in (self.plan.get("workflows") or []) if isinstance(w, dict)]
        known_ids = {str(c.get("id") or c.get("capability_id") or "").strip()
                     for c in caps}
        covered = {str(x).strip() for w in workflows for x in (w.get("covers") or [])}
        for c in caps:
            cid = str(c.get("id") or c.get("capability_id") or c.get("name") or "capability").strip()
            for rel in (c.get("files") or []):
                if isinstance(rel, dict):
                    rel = rel.get("path") or ""
                rel = str(rel or "").strip()
                if rel and not self._on_disk(rel):
                    gaps.append(f"{cid} missing capability file: {rel}")
            if bool(c.get("e2e")) and cid and cid not in covered:
                gaps.append(f"{cid} has e2e=true but no workflow covers it")
        for w in workflows:
            title = str(w.get("title") or w.get("name") or "workflow")
            for cid in (w.get("covers") or []):
                if known_ids and str(cid).strip() not in known_ids:
                    gaps.append(f"workflow {title} covers unknown capability: {cid}")
        return list(dict.fromkeys(gaps))[:40]

    def close_delivery_gaps(self, max_rounds: int = 2) -> list:
        """Ask the existing builder thread to close only fixed gaps."""
        gaps = self.delivery_gaps()
        for rnd in range(1, max_rounds + 1):
            if not gaps:
                break
            self._log("WARN", f"   🧭 delivery closure {rnd}/{max_rounds} — {len(gaps)} gap(s)")
            prompt = (
                "The accepted build plan is not fully present on disk yet. Close ONLY these deterministic delivery gaps; "
                "do not redesign or rewrite working files unless a listed gap requires it.\n" +
                "\n".join("  • " + g for g in gaps[:30]) +
                "\n\nUse workspace tools if you need to inspect an existing caller/route. "
                "Write complete missing/affected files, preserve all accepted contracts, and then stop.")
            self._run_write_loop(prompt)
            gaps = self.delivery_gaps()
        if gaps:
            # The shared thread has stalled — it is long, it has already
            # refused, and one more "continue" reproduces the same silence.
            # Each missing file gets its own clean room instead.
            written = self.close_gaps_in_clean_room(gaps)
            if written:
                gaps = self.delivery_gaps()
        return gaps

    # "missing planned file: x" / "CAP-009 missing capability file: x" /
    # "planned file is still scaffold placeholder: x"
    GAP_PATH_RE = re.compile(
        r"(?:missing planned file|missing capability file|"
        r"planned file is still scaffold placeholder):\s*(\S+)")

    def gap_paths(self, gaps: list) -> list:
        """The project-relative files a deterministic delivery gap names."""
        out = []
        for gap in gaps or []:
            m = self.GAP_PATH_RE.search(str(gap or ""))
            if not m:
                continue
            rel = m.group(1).strip().strip(";,").lstrip("./").replace("\\", "/")
            if rel and rel not in out:
                out.append(rel)
        return out

    def _planned_entry(self, rel: str) -> dict:
        """The plan's brief for one file, or a minimal stand-in."""
        for entry in self._planned_files():
            if str(entry.get("path") or "") == rel:
                return entry
        kind = "route" if re.search(r"/route\.jsx?$", rel) else "server"
        return {"path": rel, "kind": kind, "purpose": "", "sections": [],
                "actions": [], "reads": [], "writes": []}

    def close_gaps_in_clean_room(self, gaps: list, limit: int = 8) -> int:
        """Write each still-missing file in its own short conversation.

        The build thread carries every file written so far, so by the time it
        stalls, another turn in it stalls the same way. One file, one small
        prompt, the same tools — this is the pass that actually lands the
        file the plan promised.
        """
        paths = [rel for rel in self.gap_paths(gaps) if not self._on_disk(rel)]
        if not paths:
            return 0
        self._log("WARN", f"   🚑 clean-room closure — writing "
                          f"{len(paths[:limit])} file(s) the build thread "
                          f"could not produce, one at a time")
        written = 0
        for rel in paths[:limit]:
            try:
                if self.write_one_file_clean_room(rel):
                    written += 1
                    self._log("INFO", f"   🚑 clean room wrote {rel}")
                else:
                    self._log("WARN", f"   ⚠ clean room could not write {rel}")
            except Exception as e:                              # noqa: BLE001
                self._log("WARN", f"   ⚠ clean room failed on {rel}: {e}")
                log.exception("clean-room closure")
        return written

    def write_one_file_clean_room(self, rel: str) -> bool:
        """One isolated turn whose only job is this file."""
        entry = self._planned_entry(rel)
        parts = [f"Write exactly ONE file and nothing else: `{rel}`.",
                 "## Its brief, from the accepted plan\n"
                 + self._file_list_block([entry])]

        contracts = self._contract_ledger([rel])
        if contracts:
            parts.append("## Cross-file contracts it must satisfy exactly\n"
                         + contracts)
        caps = self._capability_ledger([rel])
        if caps:
            parts.append("## Capabilities it has to prove, not merely render\n"
                         + caps)

        neighbours = sorted(self._related_context_files([rel]))
        if neighbours:
            parts.append("Files already on disk that this one has to agree "
                         "with: " + ", ".join(neighbours[:8])
                         + "\nRead any of them with the read_file tool before "
                           "you write. Do not guess an interface that already "
                           "exists.")
        whitelist = self._import_whitelist_block()
        if whitelist:
            parts.append(whitelist)

        parts.append(f"Output ONE complete <write_file path=\"{rel}\">…"
                     "</write_file> block, starting immediately with "
                     "'<write_file'. No narration, no second file, no summary.")

        saved_convo = self.convo
        saved_refused = dict(getattr(self, "_refused", None) or {})
        system = (saved_convo[0] if saved_convo
                  and saved_convo[0].get("role") == "system"
                  else {"role": "system", "content": self._builder_sys()})
        try:
            self.convo = [system]
            self._refused = {}
            self._run_write_loop("\n\n".join(parts))
        finally:
            self.convo = saved_convo
            self._refused = saved_refused
        return self._on_disk(rel)

    def unfinished(self) -> list:
        """Planned files this project never produced, or `[]` if it is complete."""
        if not self.plan.get("phases"):
            return []
        return self.missing_planned_files()

    def resume(self, brief: str = "") -> bool:
        """Pick a half-finished build back up where it stopped."""
        missing = self.unfinished()
        if not self.plan.get("phases"):
            self._log("ERROR", "   ❌ No plan on disk — this project cannot be "
                               "resumed, only rebuilt")
            return False
        if not self.convo:

            # The specification, if there is one, goes back in here.
            self._log("WARN", "   ⚠ No saved conversation — resuming from the "
                              "plan" + (" and the specification" if brief
                                        else " alone"))
            self.start_conversation((self.plan.get("description")
                                     or self.plan.get("title") or "this app")
                                    + (brief or ""))

        total = sum(len(p.get("files", [])) for p in self.plan["phases"])
        self._log("INFO", f"⏭️  Resuming — {total - len(missing)}/{total} files "
                          f"already written, {len(missing)} to go")
        try:
            self._fire("on_progress", "Resuming…", 18)
            if missing:
                self.build_app()
                self._fire("on_memory", self.memory_stats())

            self._fire("on_progress", "Checking imports…", 80)
            self.repair_missing_imports()
            self.apply_next_fixes()
            self.sync_dependencies()
            self.install_unresolved()
            self.repair_lint()
            return self._verify_output()
        finally:
            self.save_convo()

    def repair_lint(self, max_rounds: int = 2) -> int:
        """Run bounded repair turns until the static findings clear."""
        if self.stack != "next":
            return 0

        total = 0
        max_rounds = max(1, int(max_rounds or 1))
        for round_no in range(1, max_rounds + 1):
            problems = [p for p in self.lint_generated()
                        if "imported but not installed" not in p]
            problems.extend(self.lint_plan_contracts())
            problems = list(dict.fromkeys(problems))
            if not problems:
                break

            title = f"Lint repair {round_no}/{max_rounds}"
            self._log(
                "WARN",
                f"🔍 {len(problems)} first-write/contract problem(s) "
                f"found — pre-handoff repair {round_no}/{max_rounds}")
            self._fire("on_phase", {"phase": -6, "title": title,
                                    "status": "active"})
            written = self._run_write_loop(textwrap.dedent(f"""\
                Deterministic first-write checks found these problems in the
                files you wrote. They are accepted-plan contracts or code
                facts, not new feature suggestions:
                {chr(10).join('  • ' + p for p in problems[:18])}

                Fix every one in this turn. Rewrite the COMPLETE file for each
                file named above, and touch no unrelated file. Preserve
                already-correct behavior and design. For
                MISSING_PLANNED_DATA/BROKEN_CONTRACT, wire the real
                collection/API edge end-to-end. For MISSING_ACTION_ID, put the
                exact literal id on the real interactive control. For
                LAYOUT_CHROME, remove app chrome from root layout and keep auth
                pages full-screen. If a file has a 'use client' part-way down,
                split it: the server half stays, the interactive half moves to
                its own file under components/ with 'use client' on line 1.

                WHEN YOU SPLIT, THE HANDLER GOES WITH THE STATE. The server half
                may pass strings, numbers, arrays and plain objects to the
                client half — never a function. If the server page currently
                passes an onSelect or onChange prop, that state belongs inside
                the new client component, which owns it and needs no prop at
                all. A server component that passes a handler compiles cleanly
                and then throws "Event handlers cannot be passed to Client
                Component props" on the first request, which is exactly the
                failure this split is supposed to prevent.
                """))
            total += written
            self._fire("on_phase", {"phase": -6, "title": title,
                                    "status": "done", "written": written})

            if written:
                total += self._fix_boundary_props()
            else:
                self._log("WARN", "   ⚠ repair pass wrote no files")
                break
        return total

    def _fix_boundary_props(self) -> int:
        """Rewrite server components caught passing a function to a client one."""
        bad = list(self.event_handlers_in_server() or [])
        bad.extend(self.bson_props_to_client() or [])
        if not bad:
            return 0
        self._log("WARN", f"🚧 {len(bad)} server/client boundary issue(s) remain — repairing")
        self._fire("on_phase", {"phase": -6, "title": "Boundary repair",
                                "status": "active"})
        n = self._run_write_loop(textwrap.dedent(f"""\
            These files still cross the Server/Client boundary with a value
            React cannot serialize. That can be a handler/function or Mongo/BSON
            data such as ObjectId. The production build can stay green while the
            requested page logs a runtime exception:
            {chr(10).join('  • ' + p for p in bad[:8])}

            Keep database reads in Server Components. Move event state into the
            client component when the problem is a handler. For Mongo data,
            convert ObjectId to strings and pass plain serializable objects or
            primitives before the JSX boundary. Rewrite only the named owner
            files and any directly involved client component.
            """))
        self._fire("on_phase", {"phase": -6, "title": "Boundary repair",
                                "status": "done", "written": n})
        return n

    # Module not found: Can't resolve 'date-fns
    UNRESOLVED_RE = re.compile(
        r"""(?:Can't resolve|Cannot find module)\s*['"]([^'"\n]+)['"]""")

    def packages_named_in(self, text: str) -> list:
        """Return bare npm packages the compiler says it cannot resolve."""
        out = []
        for spec in self.UNRESOLVED_RE.findall(text or ""):
            spec = spec.strip()
            if spec.startswith((".", "/", "@/", "~")) or "\\" in spec:
                continue
            parts = spec.split("/")
            name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            if (not name
                    or not self.PKG_NAME_RE.match(name)
                    or name in self.NODE_BUILTINS
                    or name in self.PREINSTALLED
                    or name in self.BANNED_DEPS
                    or name in out):
                continue
            out.append(name)
        return out

    def _package_spec(self, name: str) -> str:
        """Keep known packages on AgentForge's pinned version when possible."""
        try:
            data = json.loads((self.project_dir / "package.json").read_text(encoding="utf-8"))
        except Exception:
            data = {}
        declared = {**data.get("dependencies", {}),
                    **data.get("devDependencies", {})}
        version = declared.get(name) or self.EXTRA_DEPS.get(name)
        if isinstance(version, str) and re.match(r"^[\w.^~><=+*\-]+$", version):
            return f"{name}@{version}"
        return name

    def install_packages(self, names: list) -> list:
        """Install or repair compiler-named npm packages."""
        import shutil

        names = list(dict.fromkeys(n for n in names if n))
        if not names or not self.cmd:
            return []

        nm = self.project_dir / "node_modules"
        stale = []
        for name in names:
            pkg_dir = nm / name
            if pkg_dir.exists():
                stale.append(name)
                try:
                    shutil.rmtree(pkg_dir)
                except Exception as e:
                    self._log("WARN", f"   ⚠ could not clear stale {name}: {e}")

        specs = [self._package_spec(name) for name in names]
        action = "repairing" if stale else "installing"
        self._log("INFO", f"   📦 {action} {', '.join(names)}")
        res = self.cmd.run("npm install " + " ".join(specs))
        if not res.ok:
            return []

        try:
            data = json.loads((self.project_dir / "package.json").read_text(encoding="utf-8"))
            declared = set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))
            if hasattr(self, "files"):
                self.files["package.json"] = json.dumps(data, indent=2)
        except Exception:
            declared = set()

        landed = [n for n in names
                  if n in declared and (nm / n / "package.json").exists()]
        missed = [n for n in names if n not in landed]
        if missed:
            self._log("WARN", f"   ⚠ npm did not make {', '.join(missed)} usable")
        return landed

    def install_planned_deps(self) -> int:
        """Install plan dependencies that the scaffold could not pin."""
        if self.stack != "next":
            return 0
        unknown = [d.strip().split("@")[0] for d in self.plan.get("dependencies", [])
                   if isinstance(d, str) and d.strip()]
        unknown = [d for d in unknown
                   if d and d not in self.NEXT_EXTRA_DEPS
                   and d not in self.BANNED_DEPS
                   and d not in ("react", "react-dom", "next", "mongodb",
                                 "tailwindcss", "postcss", "autoprefixer")]
        if not unknown:
            return 0
        self._log("INFO", f"📦 Plan needs {', '.join(unknown)} — installing")
        res = self.cmd.run("npm install " + " ".join(unknown))
        return 1 if res.ok else 0
