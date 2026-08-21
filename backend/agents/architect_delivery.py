"""Delivery-gap repair, resume/update and package installation helpers."""
from .architect_common import *


class ArchitectDeliveryMixin:
    def run(self, user_prompt: str, *, requirement_source: str = "") -> bool:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        where = "☁️  cloud" if self.is_cloud else "💻 local"
        self._log("INFO", f"🤖 Agent mode — {self.model} ({where}, "
                          f"ctx {self.num_ctx:,})")

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
                self._log("ERROR", "   ❌ Refusing a partial delivery — " + "; ".join(remaining[:8]))
                return False
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
        return gaps

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

    def repair_lint(self) -> int:
        """Hand the static findings to the model, once."""
        if self.stack != "next":
            return 0

        problems = [p for p in self.lint_generated()
                    if "imported but not installed" not in p]
        problems.extend(self.lint_plan_contracts())
        problems = list(dict.fromkeys(problems))
        if not problems:
            return 0

        self._log("WARN", f"🔍 {len(problems)} first-write/contract problem(s) "
                          f"found — asking for one pre-handoff repair pass")
        self._fire("on_phase", {"phase": -6, "title": "Lint repair",
                                "status": "active"})
        n = self._run_write_loop(textwrap.dedent(f"""\
            Deterministic first-write checks found these problems in the files
            you wrote. They are accepted-plan contracts or code facts, not new
            feature suggestions:
            {chr(10).join('  • ' + p for p in problems[:18])}

            Fix every one in this turn. Rewrite the COMPLETE file for each file
            named above, and touch no unrelated file. Preserve already-correct
            behavior and design. For MISSING_PLANNED_DATA/BROKEN_CONTRACT, wire
            the real collection/API edge end-to-end. For MISSING_ACTION_ID, put
            the exact literal id on the real interactive control. For
            LAYOUT_CHROME, remove app chrome from root layout and keep auth pages
            full-screen. If a file has a 'use client' part-way down,
            split it: the server half stays, the interactive half moves to its
            own file under components/ with 'use client' on line 1.

            WHEN YOU SPLIT, THE HANDLER GOES WITH THE STATE. The server half
            may pass strings, numbers, arrays and plain objects to the client
            half — never a function. If the server page currently passes an
            onSelect or onChange prop, that state belongs inside the new client
            component, which owns it and needs no prop at all. A server
            component that passes a handler compiles cleanly and then throws
            "Event handlers cannot be passed to Client Component props" on the
            first request, which is exactly the failure this split is supposed
            to prevent.
            """))
        self._fire("on_phase", {"phase": -6, "title": "Lint repair",
                                "status": "done", "written": n})

        if n:
            self._fix_boundary_props()
        return n

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
