"""Server/client boundary checks, dependency resolution and package sync."""
from .architect_common import *
from pathlib import PurePosixPath


class ArchitectBoundaryMixin:
    EVENT_PROP_RE = re.compile(r"\bon[A-Z]\w*\s*=\s*{")

    def event_handlers_in_server(self) -> list:
        """Event handlers in files that are not Client Components."""
        out = []
        for path, content in self._next_files():
            if re.match(r"\s*['\"]use client['\"]", content):
                continue
            hits = sorted({m.group(0).rstrip("={ ").strip()
                           for m in self.EVENT_PROP_RE.finditer(content)})
            if hits:
                out.append(
                    f"{path}: has {', '.join(hits[:4])} but is a Server "
                    f"Component — React throws \"Event handlers cannot be "
                    f"passed to Client Component props\" on the first render. "
                    f"Move just that element into its own 'use client' "
                    f"component and render it here; do NOT add 'use client' to "
                    f"this file.")
        return out

    COMPONENT_PROP_RE = re.compile(r"\b(\w+)\s*=\s*\{\s*([A-Z]\w*)\s*\}")

    def component_props_to_client(self) -> list:
        """Server files handing a React component down as a prop."""
        out = []
        for path, content in self._next_files():
            if re.match(r"\s*['\"]use client['\"]", content):
                continue
            icons = set()
            for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*['\"]lucide-react['\"]",
                                 content):
                icons.update(n.strip().split(" as ")[-1].strip()
                             for n in m.group(1).split(",") if n.strip())
            if not icons:
                continue
            named = sorted({v for _, v in self.COMPONENT_PROP_RE.findall(content)
                            if v in icons})
            if named:
                out.append(
                    f"{path}: passes the lucide icon(s) "
                    f"{', '.join(named[:4])} as a prop from a Server "
                    f"Component. A function cannot cross into a Client "
                    f"Component — React throws \"Only plain objects can be "
                    f"passed to Client Components\" and the page still "
                    f"returns 200. Import the icons inside the client "
                    f"component, keep a name→icon map there, and pass the "
                    f"NAME as a string.")
        return out

    def bson_props_to_client(self) -> list:
        """Mongo/BSON values passed from a Server Component to a client one."""
        out = []
        suffixes = (".jsx", ".js", ".tsx", ".ts")

        def resolve(owner, spec):
            if spec.startswith("@/"):
                base = spec[2:]
            elif spec.startswith(("./", "../")):
                base = (PurePosixPath(owner).parent / spec).as_posix()
                parts = []
                for bit in base.split("/"):
                    if bit == "..":
                        if parts:
                            parts.pop()
                    elif bit not in ("", "."):
                        parts.append(bit)
                base = "/".join(parts)
            else:
                return ""
            candidates = [base] if base.endswith(suffixes) else [base + x for x in suffixes]
            candidates += [base + "/index" + x for x in suffixes]
            return next((x for x in candidates if x in self.files), "")

        for path, content in self._next_files():
            if re.match(r"\s*['\"]use client['\"]", content):
                continue
            if not re.search(r"getCollection|MongoClient|\bObjectId\b|\.findOne\s*\(|\.toArray\s*\(", content):
                continue

            client_names = set()
            for m in re.finditer(r"import\s+([A-Z]\w*)\s+from\s+['\"]([^'\"]+)['\"]", content):
                rel = resolve(path, m.group(2))
                body = self.files.get(rel, "")
                if rel and re.match(r"\s*['\"]use client['\"]", body):
                    client_names.add(m.group(1))
            for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]", content):
                rel = resolve(path, m.group(2))
                body = self.files.get(rel, "")
                if not rel or not re.match(r"\s*['\"]use client['\"]", body):
                    continue
                for item in m.group(1).split(","):
                    name = item.strip().split(" as ")[-1].strip()
                    if name and name[:1].isupper():
                        client_names.add(name)
            if not client_names:
                continue

            db_vars = set()
            for m in re.finditer(r"(?:const|let)\s+(\w+)\s*=\s*await\s+([^;\n]{1,500})", content):
                rhs = m.group(2)
                if re.search(r"getCollection|\.findOne\s*\(|\.toArray\s*\(|\.aggregate\s*\(", rhs):
                    db_vars.add(m.group(1))
            if not db_vars:
                continue

            bad = []
            for name in client_names:
                for tag in re.finditer(r"<" + re.escape(name) + r"\b([^>]*)>", content, re.S):
                    attrs = tag.group(1)
                    for prop, expr in re.findall(r"(\w+)\s*=\s*\{([^{}]+)\}", attrs):
                        expr = expr.strip()
                        roots = {v for v in db_vars if re.search(r"\b" + re.escape(v) + r"\b", expr)}
                        if not roots:
                            continue
                        safe = (".toString()" in expr or re.search(r"\bString\s*\(", expr)
                                or "JSON.stringify" in expr or "structuredClone" in expr)
                        if safe:
                            continue
                        if "._id" in expr or expr in roots:
                            bad.append(f"{name}.{prop}={{{expr}}}")
            if bad:
                out.append(
                    f"{path}: passes Mongo/BSON data to a Client Component "
                    f"({', '.join(bad[:3])}). Convert ObjectId values to strings "
                    "and Mongo documents to plain serializable objects before the JSX boundary; "
                    "keep the database read in this Server Component.")
        return out

    SERVER_ONLY_RE = re.compile(
        r"""(?:from|import)\s*\(?\s*['"](@/lib/(?:mongodb|auth|seed)"""
        r"""|next/headers|mongodb|bcryptjs)['"]""")
    CLIENT_FEATURE_RE = re.compile(
        r"\buse(?:State|Effect|Ref|Reducer|Context|Callback|Memo|Router|"
        r"Pathname|SearchParams|Params|FormState|Transition)\s*\(|\bon[A-Z]\w+\s*=\s*\{")

    # Packages that only work in the browser.
    CLIENT_ONLY_PKG_RE = re.compile(
        r"""(?:from|import)\s*\(?\s*['"](framer-motion|recharts|"""
        r"""react-chartjs-2|chart\.js|swiper|react-slick|embla-carousel-react|"""
        r"""react-hot-toast|react-toastify|@dnd-kit/\w+|react-beautiful-dnd|"""
        r"""react-leaflet|react-select|react-datepicker)['"]""")

    def client_server_mix(self) -> list:
        """`'use client'` on a file that does Server-Component-only work."""
        out = []
        for path, content in self._next_files():
            first = next((l.strip() for l in content.splitlines() if l.strip()), "")
            if not first.startswith(("'use client'", '"use client"')):
                # The other way round, and the one that compiles clean.
                srv = self.SERVER_ONLY_RE.search(content)
                cli = self.CLIENT_ONLY_PKG_RE.search(content)
                if srv and cli:
                    out.append(
                        f"{path}: imports {srv.group(1)} and {cli.group(1)} in "
                        f"one file. {cli.group(1)} only runs in the browser, so "
                        f"this file is bundled for the browser and drags the "
                        f"database driver with it — the build passes and the "
                        f"page 500s with `Can't resolve 'fs'`. Keep this a "
                        f"Server Component with the data read, and move the "
                        f"{cli.group(1)} markup into its own file under "
                        f"components/ that starts with 'use client', rendered "
                        f"from here with the data as props.")
                continue
            body = content[len(first):]
            reasons = []
            m = self.SERVER_ONLY_RE.search(body)
            if m:
                reasons.append(f"imports {m.group(1)}")
            if re.search(r"export\s+default\s+async\s+function", body):
                reasons.append("its default export is `async`, which a Client "
                               "Component may never be")
            if not reasons:
                continue
            keeps = bool(self.CLIENT_FEATURE_RE.search(body))
            out.append(
                f"{path}: is marked 'use client' but " + " and ".join(reasons) +
                ". Delete the 'use client' line and keep this file a Server "
                "Component" +
                (", then move ONLY the interactive markup — the parts with "
                 "onClick/onChange/useState — into a new file under "
                 "components/ that starts with 'use client', and render it "
                 "from here."
                 if keeps else
                 ". It uses no hooks or handlers, so nothing else has to move.")

                + " Do NOT touch next.config.mjs and do NOT add webpack "
                  "resolve.fallback — that hides this error instead of fixing "
                  "it, and the page still returns 500.")
        return out

    def broken_named_imports(self) -> list:
        """Named imports of a local module that the module does not export."""
        return group_messages(check_named_imports(self.files))

    def _stems(self) -> set:
        """Every written path with its.js/.jsx extension stripped."""
        return {re.sub(r"\.jsx?$", "", p) for p in self.files}

    def missing_planned_files(self) -> list:
        """Files the plan promised that no phase actually produced."""
        planned = [f["path"] for p in self.plan.get("phases", [])
                   for f in p.get("files", [])]
        return [p for p in planned if not self._on_disk(p)]

    def apply_next_fixes(self) -> None:
        """All mechanical repairs, in dependency order."""
        if self.stack != "next":
            return
        self.fix_next_imports()
        self.strip_server_img_handlers()
        self.enforce_use_client()
        self.name_form_fields()
        self.enforce_dynamic()
        self.verify_root_layout()
        self.verify_auth_config()

    def unresolved_packages(self) -> list:
        """Imported npm packages that are neither declared nor installed."""
        try:
            pkg = json.loads((self.project_dir / "package.json")
                             .read_text(encoding="utf-8"))
            declared = set(pkg.get("dependencies", {})) | set(pkg.get("devDependencies", {}))
        except Exception:
            declared = set()

        nm = self.project_dir / "node_modules"
        missing = []
        for path, content in self.files.items():
            if not path.endswith((".js", ".jsx")):
                continue
            for name in self.imported_packages(content):
                declared_here = name in declared
                installed_here = (nm / name / "package.json").exists()
                # Both must be true.
                if declared_here and installed_here:
                    continue
                if name not in missing:
                    missing.append(name)
        return missing

    IMPORT_SPEC_RE = re.compile(
        r"""(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(\s*|\bimport\s+)"""
        r"""['"]([^'"\s()]+)['"]""")

    PKG_NAME_RE = re.compile(r"^(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*$", re.I)

    NODE_BUILTINS = {
        "assert", "buffer", "child_process", "cluster", "console", "constants",
        "crypto", "dgram", "diagnostics_channel", "dns", "domain", "events",
        "fs", "fs/promises", "http", "http2", "https", "inspector", "module",
        "net", "os", "path", "perf_hooks", "process", "punycode", "querystring",
        "readline", "repl", "stream", "string_decoder", "timers", "tls",
        "trace_events", "tty", "url", "util", "v8", "vm", "worker_threads",
        "zlib",
    }
    PREINSTALLED = {"react", "react-dom", "next", "mongodb"}

    @classmethod
    def imported_packages(cls, content: str) -> list:
        """Npm package names a source file imports, in any syntax."""
        out = []
        for spec in cls.IMPORT_SPEC_RE.findall(content or ""):
            if spec.startswith((".", "/", "@/")):
                continue
            if spec.startswith("node:"):
                continue
            name = ("/".join(spec.split("/")[:2]) if spec.startswith("@")
                    else spec.split("/")[0])
            if (name in cls.NODE_BUILTINS or spec in cls.NODE_BUILTINS
                    or name in cls.PREINSTALLED or spec.startswith("next/")):
                continue
            if not cls.PKG_NAME_RE.match(name):
                continue
            if name not in out:
                out.append(name)
        return out

    def sync_dependencies(self) -> int:
        """Declare known npm packages imported by generated source."""
        used = set()
        for path, content in self.files.items():
            if path.endswith((".jsx", ".js", ".mjs", ".cjs", ".tsx", ".ts")):
                used.update(self.imported_packages(content))

        pkg_path = self.project_dir / "package.json"
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        deps = data.get("dependencies", {})
        added = []
        for name in sorted(used):
            if name in deps or name in data.get("devDependencies", {}):
                continue
            if name in self.BANNED_DEPS:
                self._log("WARN", f"   ⚠ Refusing to install {name} — it does "
                                  f"not belong in this stack")
                continue
            if name in self.EXTRA_DEPS:
                deps[name] = self.EXTRA_DEPS[name]
                added.append(name)
        if added:
            data["dependencies"] = dict(sorted(deps.items()))
            pkg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.files["package.json"] = json.dumps(data, indent=2)

            self.write_seq = getattr(self, "write_seq", 0) + 1
            self._log("INFO", f"   📦 Added deps: {', '.join(added)}")
        return len(added)
