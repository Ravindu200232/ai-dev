"""Output verification, existing-project loading and conversation persistence."""
from .architect_common import *


class ArchitectPersistenceMixin:
    def install_unresolved(self) -> int:
        """Let the model install anything it imported but never declared."""
        if self.stack != "next":
            return 0
        missing = self.unresolved_packages()
        if not missing:
            return 0

        # Install them rather than asking for them.
        installable = [m for m in missing if m not in self.BANNED_DEPS]
        if installable:
            self.install_packages(installable)
            missing = self.unresolved_packages()
        if not missing:
            return 0

        self._log("WARN", f"📦 {len(missing)} package(s) imported but not "
                          f"installed and npm could not supply them: "
                          f"{', '.join(missing)}")
        self._fire("on_phase", {"phase": -4, "title": "Installing packages",
                                "status": "active"})
        n = self._run_write_loop(textwrap.dedent(f"""\
            These packages are imported by the code you wrote but are not
            installed:
            {chr(10).join('  • ' + m for m in missing)}

            AgentForge already tried `npm install` for each of them and npm
            could not supply it, so these are names that do not exist on the
            registry — an import you invented, or a typo. Rewrite the file
            that imports each one to use something real, or drop the import
            and the code that needed it. Do not write any other files, and do
            not run npm install again.
            """))
        self._fire("on_phase", {"phase": -4, "title": "Installing packages",
                                "status": "done"})
        still = self.unresolved_packages()
        if still:
            self._log("WARN", f"   ⚠ Still unresolved: {', '.join(still)}")
        return n

    def _verify_output(self) -> bool:
        """Did the model actually build something, or just inherit the scaffold?"""
        entry = self._P["entry"]
        if not any(p in self.files for p in entry):
            self._log("ERROR", f"   ❌ No {entry[0]} was generated")
            return False

        if self.stack == "next":
            scaffolded = self.NEXT_SCAFFOLD | {"plan.md", "design.md"}
            generated = [p for p in self.files if p not in scaffolded]
            if len(generated) < 3:
                self._log("ERROR", f"   ❌ Only {len(generated)} file(s) beyond "
                                   f"the scaffold — the model produced nothing")
                return False
            for problem in self.lint_generated()[:6]:
                self._log("WARN", f"   ⚠ {problem}")
            self._check_auth_intact()
        return True

    def _check_auth_intact(self) -> bool:
        """Is the generated Better Auth instance still a Better Auth instance?"""
        if "app/api/auth/[...all]/route.js" not in self.files:
            return True
        try:
            src = (self.project_dir / "lib/auth.js").read_text(
                encoding="utf-8", errors="replace")
        except Exception:
            src = ""
        if "betterAuth(" in src:
            return True

        self._log("ERROR",
                  "   ❌ lib/auth.js no longer builds a Better Auth instance — "
                  "`betterAuth(` is gone, so `auth.handler` is undefined and "
                  "every /api/auth/* request will 500. Nobody can sign in, and "
                  "the pages will still look fine. Rebuild this project, or "
                  "ask for a repair naming lib/auth.js.")
        return False

    def update(self, instruction: str) -> int:
        """Agentic edit of an existing project — same tool loop, no plan."""
        self._log("INFO", f"✏️  Agent update — {instruction[:70]}")
        return self._update(instruction)

    def _update_turn(self, instruction: str) -> str:
        """The user turn an update is asked with: current source, then the ask."""
        snap = self._context_snapshot(**self._snapshot_caps())
        return (f"{SNAPSHOT_OPEN}\n{snap}\n{SNAPSHOT_CLOSE}\n\n"
                + textwrap.dedent(f"""\
                ## Change requested
                {instruction}

                Rewrite only the files that must change, complete, via
                <write_file> blocks. Create new files when the change needs
                them. Keep everything else untouched and preserve the existing
                style.

                A file shown above ending in `// …truncated…` is NOT the whole
                file. Do not rewrite one of those from what you were shown —
                you would delete the part you cannot see. Say so instead.
                """))

    def _snapshot_caps(self) -> dict:
        """How much source a snapshot may carry, from the window we actually have."""
        budget = self._budget_chars()
        if budget >= 200_000:
            return {"max_files": 40, "per_file": 24_000}
        if budget >= 80_000:
            return {"max_files": 28, "per_file": 8_000}
        return {"max_files": 18, "per_file": 2_000}

    def _update(self, instruction: str) -> int:

        if not self.convo:
            self.convo = [
                {"role": "system",
                 "content": self._builder_sys()},
                {"role": "user", "content":
                    f"Here is the existing app we are editing.\n\n"
                    f"## Plan\n{self.plan_md[:3000] or '(no plan.md)'}"},
                {"role": "assistant", "content":
                    "I have read the plan. Show me the code and tell me what "
                    "to change."},
            ]

        n = self._run_write_loop(self._update_turn(instruction))
        self.repair_missing_imports()
        self.apply_next_fixes()
        self.sync_dependencies()
        return n

    SKIP_DIRS = {"node_modules", ".git", "dist", ".vite", ".next", "out",
                 ".turbo", "public", ".agentforge", "tests"}

    SKIP_FILES = {"vitest.config.mjs", "playwright.config.js"}

    def load_existing(self):
        """Populate self.files from disk — needed before update()."""

        if (self.project_dir / "next.config.mjs").exists() or \
                (self.project_dir / "next.config.js").exists():
            self.stack = "next"
        elif (self.project_dir / "vite.config.js").exists():
            self.stack = "vite"

        for fp in self.project_dir.rglob("*"):
            if not fp.is_file() or any(s in fp.parts for s in self.SKIP_DIRS):
                continue
            if fp.name in self.SKIP_FILES:
                continue
            if fp.suffix not in (".jsx", ".js", ".mjs", ".json", ".css",
                                 ".html", ".md"):
                continue

            if fp.name.startswith(".env"):
                continue
            try:
                if fp.stat().st_size > 200_000:
                    continue
                rel = str(fp.relative_to(self.project_dir)).replace("\\", "/")
                self.files[rel] = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
        plan_fp = self.project_dir / "plan.md"
        if plan_fp.exists():
            self.plan_md = self.files.get("plan.md", "")
        if not self.plan:
            loaded_plan = self._load_plan_json()
            if loaded_plan:
                # Re-normalising on reopen upgrades old projects too.
                self.plan = self._normalise_plan(loaded_plan)
                self._save_plan_json()

        if self.load_convo():
            log.info("restored the build conversation "
                     f"({len(self.convo)} turns)")

    def _write_atomic(self, rel: str, text: str) -> None:
        """Write via a temp file and one rename."""
        fp = self.project_dir / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_suffix(fp.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, fp)

    PLAN_JSON = ".agentforge/plan.json"

    def _save_plan_json(self) -> None:
        """Keep the parsed plan next to the project."""
        if not self.plan:
            return
        try:
            self._write_atomic(self.PLAN_JSON,
                               json.dumps(self.plan, indent=2))
        except Exception as e:
            log.warning(f"could not save plan.json: {e}")

    def _load_plan_json(self) -> dict:
        try:
            fp = self.project_dir / self.PLAN_JSON
            if fp.exists():
                return json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"could not read plan.json: {e}")
        return {}

    CONVO_JSON = ".agentforge/convo.json"

    CONVO_SAVE_CHARS = 400_000

    def save_convo(self) -> bool:
        """Persist the build conversation, so a later edit inherits its memory."""
        if len(self.convo) < 3:
            return False
        try:
            self._back_up_thread_once()
            slim = []
            for m in self.convo:

                content = self._strip_snapshot(m.get("content") or "")
                content = self._stub_files(content)
                slim.append({"role": m.get("role", "user"), "content": content})

            total = sum(len(m["content"]) for m in slim)
            head, tail = slim[:3], slim[3:]
            while total > self.CONVO_SAVE_CHARS and tail:
                total -= len(tail.pop(0)["content"])

            self._write_atomic(self.CONVO_JSON, json.dumps(
                {"model": self.model, "stack": self.stack,
                 "messages": head + tail}, indent=1))
            return True
        except Exception as e:
            log.warning(f"could not save convo.json: {e}")
            return False

    CONVO_BACKUP = ".agentforge/convo.pre-strip.json"

    def _back_up_thread_once(self) -> None:
        """Keep one copy of the thread as it was before the first strip."""
        try:
            src = self.project_dir / self.CONVO_JSON
            dst = self.project_dir / self.CONVO_BACKUP
            if src.is_file() and not dst.exists():
                dst.write_text(src.read_text(encoding="utf-8"),
                               encoding="utf-8")
        except Exception as e:
            log.debug(f"convo backup: {e}")

    def load_convo(self) -> bool:
        """Restore a saved build thread."""
        if self.convo:
            return False
        try:
            fp = self.project_dir / self.CONVO_JSON
            if not fp.is_file():
                return False
            data = json.loads(fp.read_text(encoding="utf-8"))
            msgs = [m for m in (data.get("messages") or [])
                    if isinstance(m, dict) and m.get("role") and m.get("content")]
            if len(msgs) < 3:
                return False
            self.convo = msgs
            self._trim_convo()
            return True
        except Exception as e:
            log.warning(f"could not read convo.json: {e}")
            return False
