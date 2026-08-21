"""Auth completeness, layout, data-model and inert-control checks."""
from .analyzer_common import *


class AnalyzerUIMixin:
    def _signup_required(self) -> bool:
        """Only require self-registration when the accepted requirements do."""
        plan = getattr(self.arch, "plan", None) or {}
        chunks = list(plan.get("source_requirements") or [])
        for cap in plan.get("capabilities") or []:
            if isinstance(cap, dict):
                chunks.extend(str(cap.get(k) or "") for k in
                              ("requirement", "proof", "name", "title"))
        text = "\n".join(str(x or "") for x in chunks)
        # Plan_md is included only as supporting evidence.
        if not text.strip():
            text = str(getattr(self.arch, "plan_md", "") or "")
        return bool(re.search(r"\b(sign[ -]?up|signup|register|registration|create (?:an? )?account|account creation)\b",
                              text, re.I))

    def auth_completeness(self) -> list:
        """A login with no signup, or links between them that 404."""
        routes = self.enumerate_routes()
        pages = {u for u, r in routes.items() if r["kind"] == "page"}
        has_login = any("login" in u or "signin" in u for u in pages)
        if not has_login:
            return []

        managed = any(p.startswith("app/api/auth/[") for p in self.code_files())

        out = []
        if not self._signup_required():
            return out
        if not any(w in u for u in pages for w in ("signup", "register")):
            out.append(Finding(
                "major", "NO_SIGNUP",
                "there is a login page but no way to create an account",
                fix="add app/signup/page.jsx — a client page calling "
                    "signUp.email() from @/lib/auth-client"
                    if managed else
                    "add app/signup/page.jsx and app/api/auth/register/route.js"))
        if managed:
            return out

        apis = {u for u, r in routes.items() if r["kind"] == "api"}
        if not any("register" in u or "signup" in u for u in apis):
            out.append(Finding(
                "major", "NO_REGISTER_API",
                "nothing handles account creation",
                fix="add app/api/auth/register/route.js"))
        return out

    CHROME_RE = re.compile(r"<\s*(Nav\w*|Navbar|Header|Sidebar|TopBar|AppShell)\b")

    def layout_chrome(self) -> list:
        """Navigation in the root layout."""
        out = []
        files = self.code_files()
        layout = next((p for p in ("app/layout.js", "app/layout.jsx")
                       if p in files), None)
        if not layout:
            return out
        body = files[layout]
        pages = {u for u, r in self.enumerate_routes().items()
                 if r["kind"] == "page"}
        has_auth = any(w in u for u in pages for w in ("login", "signin",
                                                       "signup", "register"))
        m = self.CHROME_RE.search(body)
        if m and has_auth:
            out.append(Finding(
                "major", "LAYOUT_CHROME",
                f"the root layout renders <{m.group(1)} />, so it appears on "
                f"the login and signup screens too",
                path=layout,
                fix="remove it from app/layout.js and render it in the pages "
                    "that want it; auth pages stay full-screen"))
        if "ensureSeeded" in body:
            out.append(Finding(
                "major", "SEED_IN_LAYOUT",
                "the root layout calls ensureSeeded(), so it runs on every "
                "request including API calls",
                path=layout,
                fix="seed from the pages that read the data instead"))
        return out

    def leaks_password_hash(self) -> list:
        """A login handler returning the whole user document."""
        out = []
        for path, content in self.code_files().items():
            if "route.js" not in path or "bcrypt" not in content:
                continue
            if re.search(r"Response\.json\(\s*\{\s*[^}]*\buser\s*(?:,|\})", content) \
                    and "passwordHash" not in content.split("Response.json")[-1]:

                if re.search(r"\buser\s*=\s*await\s+\w+\.findOne", content):
                    out.append(Finding(
                        "major", "HASH_LEAK",
                        "the response returns the whole user document, so "
                        "passwordHash is sent to the browser",
                        path=path,
                        fix="return only { id, email, name, role }"))
        return out

    def credential_smells(self) -> list:
        """Static hints about a broken login."""
        out = []
        for path, content in self.code_files().items():
            for lit in BCRYPT_LITERAL_RE.findall(content):
                if len(lit) != 60:
                    out.append(Finding(
                        "blocker", "FAKE_HASH",
                        f"the string {lit[:24]}… is {len(lit)} characters; a "
                        f"bcrypt hash is exactly 60, so bcrypt.compare against "
                        f"it can never succeed and every login returns 401",
                        path=path,
                        fix="hash the real demo password at seed time with "
                            "bcrypt.hashSync(DEMO_PASSWORD, 10)"))
                    break
            if "seed" in path.lower() and "passwordHash" in content \
                    and "hashSync" not in content and "hash(" not in content:
                out.append(Finding(
                    "blocker", "UNHASHED_SEED",
                    "the seed sets passwordHash without ever calling bcrypt",
                    path=path,
                    fix="import bcrypt from 'bcryptjs' and use "
                        "bcrypt.hashSync(password, 10)"))
        return out

    def _plan_objectid_fields(self) -> set:
        """Mongo fields declared ObjectId in the approved Data Model."""
        md = self.plan_text() or ""
        fields = set()
        for name in re.findall(r"(?mi)^\s*[-*]\s*`?([A-Za-z_][\w]*)`?\s*:\s*ObjectId\b", md):
            fields.add(name)
        return fields

    def _plan_collections(self) -> set:
        """Collection names from `## Data Model` bullets."""
        md = self.plan_text() or ""
        m = re.search(r"(?ms)^## Data Model\s*$\n(.*?)(?=^## \S|\Z)", md)
        if not m:
            return set()
        out = set()
        for line in m.group(1).splitlines():
            # Collection bullets are top-level; indented bullets are fields.
            x = re.match(r"^[-*]\s*\*\*([^*]+)\*\*\s*:", line)
            if not x:
                x = re.match(r"^[-*]\s*`?([A-Za-z_][\w-]*)`?\s*:", line)
            if x:
                out.add(x.group(1).strip())
        return out

    def mongo_id_type_findings(self) -> list:
        """ObjectId/string boundary mistakes that become detail-page 404s."""
        oid_fields = self._plan_objectid_fields()
        if not oid_fields:
            return []
        out = []
        for path, body in sorted(self.code_files().items()):
            if not path.startswith(("app/", "lib/")) or path.startswith("app/api/auth/"):
                continue
            string_vars = set()
            # Params/searchParams are URL strings.
            for names in re.findall(r"const\s*\{([^}]+)\}\s*=\s*await\s+(?:params|searchParams)\b", body):
                for raw in names.split(","):
                    name = raw.split(":", 1)[-1].strip().split("=", 1)[0].strip()
                    if re.match(r"^[A-Za-z_$][\w$]*$", name):
                        string_vars.add(name)
            # Values unpacked from request JSON are transport values.
            if re.search(r"await\s+request\.json\s*\(", body):
                for names in re.findall(r"const\s*\{([^}]+)\}\s*=\s*(?:body|await\s+request\.json\s*\(\s*\))", body):
                    for raw in names.split(","):
                        name = raw.split(":", 1)[-1].strip().split("=", 1)[0].strip()
                        if re.match(r"^[A-Za-z_$][\w$]*$", name):
                            string_vars.add(name)

            for field in oid_fields:
                # Match the first query-object value for this field in common.
                rx = re.compile(
                    rf"\b(?:find|findOne|updateOne|deleteOne|findOneAndUpdate|replaceOne)\s*\(\s*\{{[^}}]{{0,500}}?\b{re.escape(field)}\s*:\s*([^,}}\n]+)",
                    re.S)
                for m in rx.finditer(body):
                    expr = m.group(1).strip()
                    if re.search(r"\b(?:new\s+)?ObjectId\s*\(", expr):
                        continue
                    if expr.endswith("._id") or re.search(r"\._id\b", expr):
                        continue
                    risky = (expr in string_vars or expr in {"id", "roomId", "userId", "ownerId"}
                             or re.fullmatch(r"(?:user|session(?:\?\.user)?|session\.user)\??\.id", expr))
                    if not risky:
                        continue
                    out.append(Finding(
                        "blocker", "MONGO_ID_TYPE", 
                        f"{path} queries ObjectId field '{field}' with string expression `{expr}`; "
                        "the query silently matches nothing, so valid detail/list data can become a 404 or empty state",
                        path=path,
                        fix=(f"convert `{expr}` with ObjectId at the Mongo boundary (and validate route ids) "
                             f"before querying `{field}`; keep outward/client ids serialized as strings"),
                        extra=[path]))
                    break
        seen, uniq = set(), []
        for f in out:
            k = (f.path, f.message)
            if k not in seen:
                seen.add(k); uniq.append(f)
        return uniq[:12]

    _DATA_URL_RE = re.compile(
        r"""["'`]data:(image|application|video|audio)/[a-z0-9.+-]+;base64,""",
        re.I)

    def upload_storage_findings(self) -> list:
        """Uploads that skip GridFS, and multipart routes that read JSON."""
        out = []
        for rel, body in self.code_files().items():
            if not isinstance(body, str) or not body:
                continue
            if not rel.startswith(("app/", "components/", "lib/")):
                continue

            if ("insertOne" in body or "insertMany" in body or
                    "updateOne" in body or "$set" in body):
                hit = self._DATA_URL_RE.search(body)
                if hit and "putFile" not in body:
                    out.append(Finding(
                        "major", "INLINE_FILE_BYTES",
                        f"{rel} writes a base64 data URL into a document — the "
                        f"row breaks once the file is real, because a document "
                        f"cannot exceed 16MB",
                        path=rel,
                        fix=("store the file in GridFS and keep only its URL "
                             "on the document: `const stored = await "
                             "putFile(file, { filename: file.name })` from "
                             "'@/lib/mongodb', then save `stored.url`. MACHINE "
                             "CHECK: this file must not contain a "
                             "'data:<type>;base64,' literal alongside a write."),
                        extra=[rel]))

            if not rel.endswith((".jsx", ".js")):
                continue
            # `readAsDataURL` is the one unambiguous tell.
            m = re.search(r"\breadAsDataURL\s*\(", body)
            if not m:
                continue
            out.append(Finding(
                "major", "UPLOAD_NOT_MULTIPART",
                f"{rel} converts the chosen file to a base64 data URL before "
                f"sending it — that inflates it by a third, defeats caching, "
                f"and puts the bytes inside a document that cannot exceed 16MB",
                path=rel,
                fix=("send the File itself as multipart and keep only the "
                     "returned url:\n"
                     "  const body = new FormData()\n"
                     "  body.append('file', event.target.files[0])\n"
                     "  const { url } = await fetch('/api/files', "
                     "{ method: 'POST', body }).then(r => r.json())\n"
                     "then save `url` on the record. MACHINE CHECK: this file "
                     "must not call readAsDataURL, and must build a FormData "
                     "for the upload."),
                extra=[rel]))
        return out

    def action_id_findings(self) -> list:
        """Workflow controls the plan promised an id for and the source lacks."""
        from .action_ids import workflow_actions, ACTION_ATTR
        out = []
        try:
            plan = getattr(self.arch, "plan", None) or {}
            rows = workflow_actions(plan)
        except Exception as e:
            log.debug(f"workflow control findings: {e}")
            return out
        if not rows:
            return out
        files = self.code_files()
        try:
            routes = self.enumerate_routes() or {}
        except Exception:
            routes = {}

        def owner(route: str) -> str:
            meta = routes.get(route) or {}
            path = str(meta.get("file") or "").replace("\\", "/")
            if path:
                return path
            for pattern, row in routes.items():
                if "[" not in pattern:
                    continue
                rx = re.escape(pattern)
                rx = re.sub(r"\\\[[^\\\]]+\\\]", r"[^/]+", rx)
                if re.fullmatch(rx, route):
                    return str((row or {}).get("file") or "").replace("\\", "/")
            return ""

        # One grep of the whole client surface: the control may
        surface = "\n".join(
            body for rel, body in files.items()
            if rel.startswith(("app/", "components/")) and isinstance(body, str))
        for r in rows:
            tid = r["testid"]
            if f'"{tid}"' in surface or f"'{tid}'" in surface:
                continue
            path = owner(r["route"]) or r["route"]
            out.append(Finding(
                "major", "MISSING_ACTION_ID",
                f"the workflow '{r['workflow']}' presses \"{r['phrase']}\" on "
                f"{r['route']}, but no control in the app carries the id that "
                f"step is identified by",
                path=path if path.endswith((".jsx", ".js")) else "",
                fix=(f"put {ACTION_ATTR}=\"{tid}\" on the element that does "
                     f"\"{r['phrase']}\" on {r['route']}. Keep whatever label "
                     f"and styling it already has — only the attribute is "
                     f"required, and it goes on the interactive element "
                     f"itself, not a wrapper. MACHINE CHECK: the literal "
                     f"{ACTION_ATTR}=\"{tid}\" must appear in the page or a "
                     f"component it renders. If the control genuinely does "
                     f"not exist yet, build it."),
                extra=[path] if path.endswith((".jsx", ".js")) else []))
        return out

    def planned_data_findings(self) -> list:
        """A planned data obligation may be direct Mongo OR a real API hand-off."""
        plan = getattr(self.arch, "plan", None) or {}
        files = self.code_files()
        collections = self._plan_collections()
        out = []

        AUTH_ALIASES = {"users": "user", "sessions": "session",
                        "accounts": "account"}

        def _norm(name) -> str:
            n = str(name or "").strip()
            return AUTH_ALIASES.get(n.lower(), n)

        def _colls(body: str) -> set:
            got = set()
            for m in re.finditer(r"(?:getCollection|\.collection)\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", body or ""):
                got.add(_norm(m.group(1)))
            return got

        def _closure(path: str, seen: set = None) -> str:
            # The page plus every project file it imports, one blob.
            from . import exports as _ex
            seen = seen if seen is not None else set()
            if not path or path in seen or path not in files:
                return ""
            seen.add(path)
            body = files.get(path) or ""
            if not isinstance(body, str):
                return ""
            parts = [body]
            for stmt in _ex.parse_imports(body):
                target = _ex.resolve_local(path, stmt.spec, files)
                if target:
                    parts.append(_closure(target, seen))
            return "\n".join(parts)

        def _api_file_for(url: str) -> str:
            clean = str(url or "").split("?", 1)[0].strip()
            if not clean.startswith("/api/"):
                return ""
            segs = [x for x in clean[len('/api/'):].strip('/').split('/') if x]
            exact = "app/api/" + "/".join(segs) + "/route.js"
            if exact in files:
                return exact
            # Dynamic URL literals normally contain ${id}.
            cands = []
            for rel in files:
                if not rel.startswith("app/api/") or not rel.endswith("/route.js"):
                    continue
                mid = rel[len("app/api/"):-len("/route.js")]
                parts = mid.split('/') if mid else []
                if len(parts) != len(segs):
                    continue
                ok = True
                for a, b in zip(parts, segs):
                    if a.startswith('[') and a.endswith(']'):
                        continue
                    if a != b and '${' not in b:
                        ok = False; break
                if ok:
                    cands.append(rel)
            return sorted(cands)[0] if cands else ""

        def _api_edges(body: str) -> list[tuple[str, str]]:
            edges = []
            # Capture the common output shape.
            rx = re.compile(r"fetch\s*\(\s*([`'\"])(/api/.*?)(?:\1)\s*(?:,\s*\{(.*?)\})?\s*\)", re.S)
            for m in rx.finditer(body or ""):
                url = m.group(2)
                opts = m.group(3) or ""
                mm = re.search(r"method\s*:\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]", opts, re.I)
                method = (mm.group(1).upper() if mm else "GET")
                edges.append((url, method))
            for url in re.findall(r"['\"](/api/[A-Za-z0-9_./${}\[\]-]+)['\"]", body or ""):
                if not any(u == url for u, _ in edges):
                    edges.append((url, "GET"))
            return edges

        for ph in plan.get("phases") or []:
            for spec in ph.get("files") or []:
                if not isinstance(spec, dict):
                    continue
                path = str(spec.get("path") or "")
                body = files.get(path, "")
                if not body:
                    continue
                reads = [str(x) for x in spec.get("reads") or [] if str(x)]
                writes = [str(x) for x in spec.get("writes") or [] if str(x)]
                # Backward-compatible recovery from old purpose prose.
                if not reads and collections:
                    purpose = str(spec.get("purpose") or "").lower()
                    if "read" in purpose:
                        reads = [c for c in collections if re.search(rf"\b{re.escape(c.lower())}\b", purpose)]

                direct = _colls(_closure(path))
                api_read, api_write = set(), set()
                for url, method in _api_edges(_closure(path)):
                    rel = _api_file_for(url)
                    if not rel:
                        continue
                    touched = _colls(files.get(rel, ""))
                    api_read |= touched
                    if method != "GET":
                        api_write |= touched

                for coll in dict.fromkeys(reads):
                    if _norm(coll) not in direct and _norm(coll) not in api_read:
                        api_hint = f"app/api/{str(coll).strip().lower()}/route.js"
                        route_hint = api_hint if api_hint not in files else "the page's existing API route"
                        api_url = f"/api/{str(coll).strip().lower()}"
                        out.append(Finding(
                            "major", "MISSING_PLANNED_DATA",
                            f"the plan says {path} reads Mongo collection '{coll}', but neither the file nor an API route it calls reaches that collection",
                            path=path,
                            fix=(f"wire the planned {coll} read end to end. If no suitable API exists, "
                                 f"create {route_hint} with a GET that reads collection '{coll}', then "
                                 f"make {path} call it. MACHINE CHECK: {path} (or a component it "
                                 f"imports) must contain either fetch('{api_url}') as a literal, or "
                                 f"getCollection('{coll}') directly. Any other wiring will fail this "
                                 f"check again."),
                            extra=[path, api_hint]))
                for coll in dict.fromkeys(writes):
                    if _norm(coll) not in direct and _norm(coll) not in api_write:
                        api_hint = f"app/api/{str(coll).strip().lower()}/route.js"
                        api_url = f"/api/{str(coll).strip().lower()}"
                        out.append(Finding(
                            "major", "MISSING_PLANNED_DATA",
                            f"the plan says {path} writes Mongo collection '{coll}', but neither the file nor a mutation API it calls reaches that collection",
                            path=path,
                            fix=(f"wire the planned write to {coll} end to end. If the mutation route is "
                                 f"missing, create {api_hint} and call it from {path}. MACHINE CHECK: "
                                 f"{path} (or a component it imports) must contain a fetch to "
                                 f"'{api_url}' with a non-GET method, or write via "
                                 f"getCollection('{coll}') directly."),
                            extra=[path, api_hint]))
        return out[:12]

    def inert_control_findings(self) -> list:
        """Visible controls that are permanently inert are unfinished UI."""
        out = []
        button_re = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.S | re.I)
        for path, body in sorted(self.code_files().items()):
            if not path.startswith(("app/", "components/")) or not path.endswith((".jsx", ".js")):
                continue
            # Explicit dead-link and empty handlers are always real defects.
            if re.search(r"href\s*=\s*['\"]#['\"]", body):
                out.append(Finding("major", "INERT_CONTROL",
                                   f"{path} renders href='#' — a visible link that cannot reach a real page",
                                   path=path, fix="point it at a planned route or remove the control", extra=[path]))
            if re.search(r"onClick\s*=\s*\{\s*\(?.*?\)?\s*=>\s*\{\s*\}\s*\}", body, re.S):
                out.append(Finding("major", "INERT_CONTROL",
                                   f"{path} contains an empty onClick handler",
                                   path=path, fix="implement the action named by the control or remove it", extra=[path]))
            for m in button_re.finditer(body):
                attrs, inner = m.group(1), m.group(2)
                label = re.sub(r"<[^>]+>", " ", inner)
                label = " ".join(re.sub(r"\{[^{}]*\}", " ", label).split())[:80] or "button"
                # Disabled={loading} is state, bare `disabled` is permanent.
                if re.search(r"(?:^|\s)disabled(?:\s|>|$)", attrs) and not re.search(r"disabled\s*=\s*\{", attrs):
                    out.append(Finding(
                        "major", "INERT_CONTROL",
                        f"{path} renders permanently disabled '{label}' — the UI advertises an action nobody can perform",
                        path=path, fix="implement the action or remove the control instead of shipping a permanent placeholder",
                        extra=[path]))
                    continue
                if re.search(r"\bonClick\s*=", attrs) or re.search(r"\btype\s*=\s*['\"]submit['\"]", attrs, re.I):
                    continue
                # A button inside a form may legitimately rely on default
                before = body[:m.start()]
                inside_form = before.rfind("<form") > before.rfind("</form>")
                if inside_form:
                    continue
                out.append(Finding(
                    "major", "INERT_CONTROL",
                    f"{path} renders '{label}' as a button with no click handler and no form submission",
                    path=path, fix="wire the button to its real action or remove it", extra=[path]))
        seen, uniq = set(), []
        for f in out:
            key = (f.path, f.message)
            if key not in seen:
                seen.add(key); uniq.append(f)
        return uniq[:12]
