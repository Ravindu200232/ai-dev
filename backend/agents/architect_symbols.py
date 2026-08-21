"""Symbol ledgers, import repair, command execution and client/server directives."""
from .architect_common import *


class ArchitectSymbolMixin:
    DEFAULT_EXPORT_RE = re.compile(
        r"""export\s+default\s+(?:async\s+)?(?:function|class)\s+(\w+)""")
    # Only the name and the position of the opening brace.
    PROPS_RE = re.compile(
        r"""(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(\s*\{""")
    ARROW_PROPS_RE = re.compile(
        r"""(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(\s*\{""")

    @staticmethod
    def _balanced(body: str, open_at: int) -> str:
        """The text inside the braces starting at `open_at`, nesting included."""
        depth, out = 0, []
        for ch in body[open_at:]:
            if ch == "{":
                depth += 1
                if depth == 1:
                    continue
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return "".join(out)
            if depth >= 1:
                out.append(ch)
        return ""

    def _props_in(self, body: str) -> dict:
        """Component name -> the prop names it destructures, top level only."""
        found = {}
        for rx in (self.PROPS_RE, self.ARROW_PROPS_RE):
            for hit in rx.finditer(body):
                raw = self._balanced(body, hit.end() - 1)
                if not raw:
                    continue
                taken, depth, part = [], 0, []
                for ch in raw:  # Split on top-level commas only
                    if ch in "{[(":
                        depth += 1
                    elif ch in "}])":
                        depth -= 1
                    if ch == "," and depth == 0:
                        taken.append("".join(part))
                        part = []
                    else:
                        part.append(ch)
                taken.append("".join(part))

                names = []
                for one in taken:
                    one = one.strip().split(":")[0].split("=")[0].strip()
                    one = one.lstrip(".").strip()  # ...rest -> rest
                    if one and one.isidentifier():
                        names.append(one)
                if names:
                    found[hit.group(1)] = names
        return found

    def _symbol_ledger(self, limit: int = 60) -> str:
        """What every file written so far exports, and what props it takes."""
        rows = []
        for path in sorted(self.files):
            if not path.endswith((".js", ".jsx")) or not self.is_source(path):
                continue
            body = self.files.get(path) or ""

            names = set(self.DECL_EXPORT_RE.findall(body))
            for group in self.LIST_EXPORT_RE.findall(body):
                for entry in group.split(","):
                    entry = entry.strip()
                    if entry:
                        names.add(re.split(r"\s+as\s+", entry)[-1].strip())

            default = ""
            hit = self.DEFAULT_EXPORT_RE.search(body)
            if hit:
                default = hit.group(1)
            else:
                bare = re.search(r"export\s+default\s+(\w+)\s*[;\n]", body)
                if bare:
                    default = bare.group(1)
                elif re.search(r"export\s+default\b", body):
                    default = "(anonymous)"

            props = self._props_in(body)

            bits = []
            if default:
                own = props.get(default)
                bits.append(f"default {default}"
                            + (f"({', '.join(own)})" if own else ""))
            for n in sorted(names - {default}):
                own = props.get(n)
                bits.append(n + (f"({', '.join(own)})" if own else ""))
            if bits:
                rows.append(f"  {path} — " + "; ".join(bits))

        if not rows:
            return ""
        head = ("What you have written so far, and the shape of each one. Props "
                "are in brackets: render a component with exactly these names, "
                "and import only what is listed here.")
        if len(rows) > limit:
            rows = rows[:limit] + [f"  … and {len(rows) - limit} more on disk"]
        return head + "\n" + "\n".join(rows)

    def _resolves(self, target: str) -> bool:
        """Does a root-relative import specifier point at a file we have?"""
        return any(c in self.files for c in
                   (target, f"{target}.js", f"{target}.jsx",
                    f"{target}/index.js", f"{target}/index.jsx"))

    def redirect_dead_imports(self) -> int:
        """Repoint an import whose path does not exist at the file that really exports."""
        exporters = self._named_exporters()
        fixed = 0
        for path, content in list(self.files.items()):
            if not path.endswith((".js", ".jsx")):
                continue
            base = Path(path).parent
            out = content
            for m in self.NAMED_IMPORT_RE.finditer(content):
                spec = m.group(3)
                if spec.startswith("."):
                    target = self._normalise((base / spec).as_posix())
                elif spec.startswith("@/"):
                    target = self._normalise(spec[2:])
                else:
                    continue
                if not target or self._resolves(target):
                    continue

                symbols = [re.split(r"\s+as\s+", s.strip())[0].strip()
                           for s in m.group(1).split(",") if s.strip()]
                if not symbols:
                    continue

                homes = set.intersection(*(exporters.get(s, set())
                                           for s in symbols)) \
                    if all(s in exporters for s in symbols) else set()
                homes.discard(path)
                if len(homes) != 1:
                    continue

                home = homes.pop()
                alias = "@/" + re.sub(r"\.jsx?$", "", home)
                out = out.replace(m.group(0),
                                  m.group(0).replace(f"{m.group(2)}{spec}"
                                                     f"{m.group(2)}",
                                                     f"{m.group(2)}{alias}"
                                                     f"{m.group(2)}"))
                fixed += 1
                self._log("INFO", f"   🔗 {path}: {spec} does not exist — "
                                  f"{', '.join(symbols)} comes from {home}")
            if out != content:
                self.write_file(path, out)
        return fixed

    def repair_missing_imports(self) -> int:
        """Generate any local module that is imported but was never written."""
        self.redirect_dead_imports()

        def _ext_for(t: str) -> str:
            if self.stack == "vite":
                return ".jsx"
            return ".js" if self.canonical_path(t + ".js").endswith(".js") else ".jsx"

        missing = set()
        for path, content in list(self.files.items()):
            if not path.endswith((".jsx", ".js")):
                continue
            base = Path(path).parent
            specs = [(base / s).as_posix() for s in self.LOCAL_IMPORT_RE.findall(content)]
            specs += self.ALIAS_IMPORT_RE.findall(content)
            for target in specs:
                target = self._normalise(target)
                if not target or target.endswith(".css"):
                    continue
                cands = [target, f"{target}.js", f"{target}.jsx",
                         f"{target}/index.js", f"{target}/index.jsx"]
                if not any(c in self.files for c in cands):
                    missing.add(self.canonical_path(target)
                                if target.endswith((".jsx", ".js"))
                                else f"{target}{_ext_for(target)}")

        if not missing:
            return 0

        # Asking for a file the prompt forbids writing gets a refusal, and the
        # refusal streams into the log a token per line. The import is the
        # defect: this app has no sessions, so the page must stop asking for
        # one rather than have `lib/auth.js` invented for it.
        forbidden = sorted(m for m in missing if m in self.OWNED_FILES)
        if forbidden:
            self._log("WARN", f"   ⛔ {', '.join(forbidden)} cannot be created — "
                              f"AgentForge owns those and this app has no "
                              f"sign-in. The import is the defect, not the "
                              f"missing file.")
            missing = [m for m in missing if m not in self.OWNED_FILES]
            if not missing:
                return 0

        self._log("WARN", f"🔗 {len(missing)} imported file(s) missing — generating")
        self._fire("on_phase", {"phase": -1, "title": "Repairing imports",
                                "status": "active"})

        n = self._run_write_loop(textwrap.dedent(f"""\
            These modules are imported by the app but were never written:
            {chr(10).join('  • ' + m for m in sorted(missing))}

            Create each one so every import resolves. Match the visual style
            and the named/default exports the importing files expect —
            you wrote those imports, so use what they ask for.
            One <write_file> block per file.
            """))
        self._fire("on_phase", {"phase": -1, "title": "Repairing imports",
                                "status": "done", "written": n})
        return n

    # Written by the scaffold when the app has sign-in, and never by a model.
    # A missing one of these means the app has no auth at all.
    OWNED_FILES = frozenset({
        "lib/auth.js", "lib/auth-client.js",
        "app/api/auth/[...all]/route.js", "lib/mongodb.js",
    })

    def run_requested_commands(self, reply: str) -> list:
        """Execute every <run_command> block in a model reply."""
        commands = [c.strip() for c in CMD_RE.findall(reply or "")]
        commands = [c for c in commands if c]
        if not commands:
            return []

        out = []
        for command in commands[:5]:

            if self._already_satisfied(command):
                self._log("INFO", f"   ↩ skipped (already installed): {command}")
                out.append(f"$ {command}\n[skipped — already installed]")
                continue
            out.append(self.cmd.run(command).as_feedback())
        return out

    _INSTALL_RE = re.compile(r"^\s*(npm|yarn|pnpm)\s+(install|i|add)\s+(.+)$", re.I)

    @staticmethod
    def _pkg_name(spec: str) -> str:
        """`bcryptjs@^2.4.3` → `bcryptjs`; `@scope/pkg@1.0` → `@scope/pkg`."""
        spec = spec.strip()
        if spec.startswith("@"):
            return "@" + spec[1:].split("@", 1)[0]
        return spec.split("@", 1)[0]

    def _already_satisfied(self, command: str) -> bool:
        """True when an install would be a no-op — each one costs ~a minute."""
        m = self._INSTALL_RE.match(command or "")
        if not m:
            return False
        names = [self._pkg_name(p) for p in m.group(3).split()
                 if not p.startswith("-")]
        if not names:
            return False
        nm = self.project_dir / "node_modules"
        return all((nm / n / "package.json").exists() for n in names)

    CLIENT_HINTS = re.compile(
        r"\buse(State|Effect|Ref|Reducer|Context|Memo|Callback|Router|Pathname)\s*\("
        r"|on(Click|Change|Submit|Input|KeyDown|KeyUp|Focus|Blur)\s*="
        r"|from\s+['\"]framer-motion['\"]"
        r"|\b(window|document|localStorage)\s*\.")
    IMPORT_LINE_RE = re.compile(r"^\s*import\s.+$", re.M)

    def _next_files(self):
        for path, content in list(self.files.items()):
            if path in self.NEXT_PROTECTED or not path.endswith((".js", ".jsx")):
                continue
            if path.startswith(("app/", "components/", "lib/")):
                yield path, content

    IMG_HANDLER_RE = re.compile(
        r"\s*\bon(?:Error|Load)\s*=\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")

    def strip_server_img_handlers(self) -> int:
        """Delete image event handlers from Server Components."""
        fixed = 0
        for path, content in list(self._next_files()):
            if re.match(r"\s*['\"]use client['\"]", content):
                continue
            out, n = self.IMG_HANDLER_RE.subn("", content)
            if n:
                self.write_file(path, out)
                fixed += n
                self._log("INFO", f"   🔧 Removed {n} image handler(s) from "
                                  f"{path} — it is a Server Component")
        return fixed

    def enforce_use_client(self) -> int:
        """Prepend `'use client'` where the file clearly needs it."""
        fixed = 0
        for path, content in self._next_files():
            if self.DIRECTIVE_RE.match(content):
                continue

            if self.STRAY_DIRECTIVE_RE.search(content):
                self._log("WARN", f"   ⚠ {path} has a misplaced 'use client' — "
                                  f"leaving it for the fix pass")
                continue
            if path.endswith("route.js") or "/route.js" in path:
                continue
            if not self.CLIENT_HINTS.search(content):
                continue
            if re.search(r"^\s*export\s+const\s+metadata\b", content, re.M):

                self._log("WARN", f"   ⚠ {path} needs 'use client' but exports "
                                  f"metadata — leaving it for the fix pass")
                continue
            self.write_file(path, "'use client'\n\n" + content)
            fixed += 1
        if fixed:
            self._log("INFO", f"   🔧 Added 'use client' to {fixed} file(s)")
        return fixed

    DIRECTIVE_RE = re.compile(r"""\A\s*(['"])use (?:client|server)\1\s*;?[^\S\n]*\n""")

    def name_form_fields(self) -> int:
        """Name every form control the model left anonymous."""
        from .architect_next_rules import name_unnamed_fields
        fixed, shown = 0, []
        for path, content in self._next_files():
            if not path.endswith((".jsx", ".tsx")):
                continue
            new, notes = name_unnamed_fields(content)
            if not notes:
                continue
            self.write_file(path, new)
            fixed += len(notes)
            if len(shown) < 3:
                shown.append(f"{path}: {notes[0]}")
        if fixed:
            self._log("INFO", f"   🔧 Named {fixed} anonymous form control(s) "
                              f"so a test can find them — " + "; ".join(shown))
        return fixed

    def enforce_dynamic(self) -> int:
        """DB-reading routes must opt out of static prerendering."""
        fixed = 0
        for path, content in self._next_files():
            if not re.match(r"^app/(.+/)?(page|route)\.jsx?$", path):
                continue
            if "@/lib/mongodb" not in content:
                continue
            if re.search(r"^\s*export\s+const\s+dynamic\b", content, re.M):
                continue

            m = self.DIRECTIVE_RE.match(content)
            at = m.end() if m else 0
            patched = (content[:at] + "export const dynamic = 'force-dynamic'\n\n"
                       + content[at:])
            self.write_file(path, patched)
            fixed += 1
        if fixed:
            self._log("INFO", f"   🔧 Added force-dynamic to {fixed} route(s)")
        return fixed
