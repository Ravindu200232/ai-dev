"""Workflow capability analysis, credential verification and login endpoint checks."""
from .analyzer_common import *


class AnalyzerWorkflowMixin:
    def auth_flow_findings(self) -> list:
        """Role-aware apps must not authenticate everybody into the public root."""
        plan = getattr(self.arch, "plan", None) or {}
        roles = {str(a.get("role") or "").lower() for a in plan.get("demo_accounts") or []
                 if isinstance(a, dict) and a.get("role")}
        if len(roles) < 2:
            return []
        files = self.code_files()
        out = []
        for path, body in files.items():
            if not re.search(r"app/(?:login|sign-in|signin)/page\.jsx?$", path):
                continue
            if "signIn.email" not in body:
                continue
            hard_root = re.search(r"router\.(?:push|replace)\(\s*['\"]/['\"]\s*\)", body)
            role_logic = re.search(r"(?:data|session|user)(?:\?\.)?\.role|\brole\s*===?", body)
            if hard_root and not role_logic:
                out.append(Finding(
                    "major", "ROLE_REDIRECT",
                    f"{path} signs in a multi-role app but hard-codes every successful login to /; role-specific users can authenticate correctly and still land in the wrong area",
                    path=path,
                    fix="route successful login by the returned/session user role to each role's planned landing page",
                    extra=[path]))
        return out


    def workflow_control_findings(self) -> list:
        """Quoted workflow clicks must exist in the page/component source."""
        plan = getattr(self.arch, "plan", None) or {}
        files = self.code_files()
        if not files:
            return []

        def route_file(route: str) -> str:
            route = str(route or "").strip().split('?', 1)[0]
            if not route.startswith('/'):
                return ""
            if route == '/':
                cands = ['app/page.jsx', 'app/page.js']
            else:
                tail = route.strip('/')
                cands = [f'app/{tail}/page.jsx', f'app/{tail}/page.js']
            for c in cands:
                if c in files:
                    return c
            # Dynamic route strings in the machine plan already use [id].
            return ""

        def imports(rel: str) -> list[str]:
            body = files.get(rel, "")
            out = []
            for spec in re.findall(r"(?:from\s+|import\s*\()\s*['\"](@/[^'\"]+|\.{1,2}/[^'\"]+)['\"]", body):
                if spec.startswith('@/'):
                    base = spec[2:]
                else:
                    base = str((Path(rel).parent / spec).as_posix())
                    while '/./' in base:
                        base = base.replace('/./','/')
                base = re.sub(r"\.(?:jsx?|tsx?)$", "", base)
                for ext in ('.jsx','.js','/index.jsx','/index.js'):
                    got = base + ext
                    if got in files:
                        out.append(got); break
            return out

        def closure(rel: str) -> list[str]:
            seen, q = set(), [(rel, 0)]
            while q:
                cur, depth = q.pop(0)
                if cur in seen or cur not in files or depth > 2:
                    continue
                seen.add(cur)
                if depth < 2:
                    q.extend((x, depth + 1) for x in imports(cur))
            return list(seen)

        def norm_words(text: str) -> set[str]:
            text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
            stop = {'click','the','a','an','button','link','to','and','on','of'}
            return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in stop}

        out = []
        seen = set()
        for wf in plan.get('workflows') or []:
            if not isinstance(wf, dict):
                continue
            for step in wf.get('steps') or []:
                step = str(step or '')
                # Accepted planner grammar: /route — click 'Control' — outcome
                m = re.search(r"^\s*(/[^—\n]*)\s*—\s*click\s+['\"]([^'\"]+)['\"]", step, re.I)
                if not m:
                    continue
                route, label = m.group(1).strip(), m.group(2).strip()
                owner = route_file(route)
                if not owner:
                    continue
                wanted = norm_words(label)
                if not wanted:
                    continue
                bundle = closure(owner)
                hay = ' '.join(files.get(x, '') + ' ' + x for x in bundle)
                got = norm_words(hay)
                if wanted <= got or len(wanted & got) >= max(1, len(wanted) - 1):
                    continue
                key = (owner, label.lower())
                if key in seen:
                    continue
                seen.add(key)
                out.append(Finding(
                    'major', 'MISSING_WORKFLOW_CONTROL',
                    f"workflow '{wf.get('name') or 'journey'}' requires clicking '{label}' on {route}, but that action is not present in the page/component source closure",
                    path=owner,
                    fix=f"implement the planned '{label}' action with an accessible name/testid and its real business effect",
                    extra=[x for x in bundle if x != owner][:3]))
        return out[:10]

    def capability_shape_findings(self) -> list:
        """Machine-plan completeness before semantic proof is attempted."""
        plan = getattr(self.arch, "plan", None) or {}
        caps = [c for c in plan.get("capabilities") or [] if isinstance(c, dict)]
        if not caps:
            return []
        planned = {f.get("path") for ph in plan.get("phases") or []
                   for f in ph.get("files") or [] if isinstance(f, dict)}
        covered = {str(cid).upper() for w in plan.get("workflows") or [] if isinstance(w, dict)
                   for cid in (w.get("covers") or [])}
        out = []
        for c in caps:
            cid = str(c.get("id") or "capability")
            cfiles = [str(x) for x in c.get("files") or []]
            missing = [x for x in cfiles if x not in planned]
            if not cfiles or missing:
                out.append(Finding(
                    "blocker", "CAPABILITY_UNMAPPED",
                    f"{cid} '{c.get('requirement','')}' has no complete planned file map" +
                    (f"; unplanned: {', '.join(missing)}" if missing else ""),
                    path=(cfiles[0] if cfiles else ""),
                    fix="repair the plan/build so every capability names the files that implement it",
                    extra=[x for x in cfiles if x]))
            if c.get("e2e", True) and cid.upper() not in covered:
                out.append(Finding(
                    "major", "CAPABILITY_UNWALKED",
                    f"{cid} '{c.get('requirement','')}' is user-visible but no E2E workflow covers it",
                    path=(cfiles[0] if cfiles else ""),
                    fix="add the capability to a workflow that actually performs and asserts it",
                    extra=[x for x in cfiles if x]))
        return out[:12]

    def scan(self) -> AnalyzerReport:
        r = AnalyzerReport()
        r.planned = self.planned_paths()
        r.missing = self.missing_files()
        r.routes = self.enumerate_routes()
        r.dead_links = self.dead_links(r.routes)
        r.unresolved = self.unresolved_packages()

        plan_lines = self.plan_text().splitlines()
        for p in r.missing:
            why = next((ln.strip(" *-\t") for ln in plan_lines if f"`{p}`" in ln), "")
            stub = self._is_placeholder(p)
            r.findings.append(Finding(
                "blocker", "MISSING_FILE",
                "this is still the scaffold placeholder — the real page was "
                "never written" if stub else
                "the plan promises this file but it was never written",
                path=p,
                fix=(f"write it. The plan says: {why[:160]}" if why
                     else "write it, matching the plan")))

        for f in self.missing_local_imports():
            r.findings.append(f)
        for f in self.broken_imports():
            r.findings.append(f)

        r.findings.extend(self.async_param_confusion())
        r.findings.extend(self.session_cookie_mismatch())
        r.findings.extend(self.unique_index_in_seed())
        r.findings.extend(self.prop_contract_breaks())
        r.findings.extend(self.credentials_exposed())
        r.findings.extend(self.seed_volume())
        r.findings.extend(self.mongo_id_type_findings())
        r.findings.extend(self.planned_data_findings())
        r.findings.extend(self.inert_control_findings())
        r.findings.extend(self.auth_flow_findings())
        r.findings.extend(self.capability_shape_findings())
        r.findings.extend(self.workflow_control_findings())
        r.findings.extend(self.action_id_findings())
        r.findings.extend(self.upload_storage_findings())

        for url in self.dead_endpoints(r.routes):
            handler = "app" + url + "/route.js"
            r.findings.append(Finding(
                "blocker", "DEAD_ENDPOINT",
                f"something fetches {url} but no route handler serves it, so "
                f"the call 404s — a client component that redirects when the "
                f"fetch fails will bounce the user out of the page",
                fix=f"write {handler}",
                extra=[handler]))

        r.findings.extend(self.contract_findings(r.routes))

        for url in r.dead_links:
            r.findings.append(Finding(
                "major", "DEAD_LINK",
                f"something links to {url} but no page serves it — a 404",
                fix=f"either create the page for {url} or remove the link"))

        orphans = self.unreachable_pages(r.routes)
        if orphans:
            shown = ", ".join(orphans[:8]) + ("…" if len(orphans) > 8 else "")

            files = self.code_files()
            repair_paths = []

            def add_path(rel):
                rel = str(rel or "").lstrip("./").replace("\\", "/")
                if rel and rel in files and rel not in repair_paths:
                    repair_paths.append(rel)

            add_path("components/Navbar.jsx")
            add_path("components/Navbar.js")
            add_path("app/page.jsx")
            add_path("app/page.js")

            contracts = (getattr(self.arch, "plan", None) or {}).get("contracts") or []
            for url in orphans[:12]:
                meta = r.routes.get(url) or {}
                add_path(meta.get("file"))

                for c in contracts:
                    if not isinstance(c, dict):
                        continue
                    target = str(c.get("target") or "").rstrip("/") or "/"
                    if target == (url.rstrip("/") or "/"):
                        add_path(c.get("from"))

                parent = url.rstrip("/")
                while "/" in parent.strip("/"):
                    parent = parent.rsplit("/", 1)[0] or "/"
                    pm = r.routes.get(parent) or {}
                    if pm.get("kind") == "page":
                        add_path(pm.get("file"))
                        break

            r.findings.append(Finding(
                "blocker", "NO_WAY_THERE",
                f"{len(orphans)} page(s) exist that nothing links to, so "
                f"nobody can reach them: {shown}",
                path=(repair_paths[0] if repair_paths else ""),
                fix="wire the planned navigation without changing page chrome: "
                    "put top-level role links in components/Navbar.jsx and render "
                    "that navbar on the applicable pages; link nested create/detail "
                    "routes from their parent list/card controls. Do NOT move the "
                    "navbar into the root layout when login/signup are meant to "
                    "stay bare",
                extra=repair_paths[1:12]))

        r.findings.extend(self.bad_objectid())
        r.findings.extend(self.unawaited_collection())

        for loc in self.stray_directives():
            r.findings.append(Finding(
                "blocker", "STRAY_DIRECTIVE",
                "a 'use client' directive appears after other code, which "
                "fails to compile",
                path=loc.split(":")[0],
                fix="split the file: the server half stays, the interactive "
                    "half moves to its own file under components/ with "
                    "'use client' on line 1"))

        for name in r.unresolved:
            r.findings.append(Finding(
                "blocker", "MISSING_PACKAGE",
                f"'{name}' is imported but not installed",
                fix=f"npm install {name}"))

        r.findings.extend(self.credential_smells())
        r.findings.extend(self.seed_race())
        r.findings.extend(self.stale_seed_guard())
        r.findings.extend(self.authz_redirect())
        r.findings.extend(self.seed_behind_auth())
        r.findings.extend(self.auth_origin())
        r.findings.extend(self.session_user_id())
        r.findings.extend(self.auth_completeness())
        r.findings.extend(self.layout_chrome())
        r.findings.extend(self.leaks_password_hash())

        try:
            seen = {f.path for f in r.findings}
            for problem in self.arch.lint_generated():
                path = problem.split(":")[0]
                if path in seen or "imported but not installed" in problem:
                    continue
                if any(problem.startswith(p) for p in r.missing):
                    continue
                r.findings.append(Finding(
                    "major", "LINT", problem, path=path,
                    fix=(f"repair {path} so the deterministic lint rule closes; "
                         "preserve existing behaviour and keep Server/Client "
                         "Component boundaries valid")))
        except Exception as e:
            log.warning(f"lint_generated failed: {e}")

        return r

    def find_login_endpoint(self) -> str:
        """The URL that verifies a password."""
        routes = self.enumerate_routes()
        api = {u: r for u, r in routes.items() if r["kind"] == "api"}
        files = self.source_files()

        for url, r in api.items():
            body = files.get(r["file"], "")
            if "bcrypt.compare" in body or "compareSync" in body:
                return url

        for url, r in api.items():
            body = files.get(r["file"], "")
            if "better-auth" in body and "POST" in r["methods"]:
                base = re.sub(r"/\[\.\.\.[^\]]+\]$", "", url).rstrip("/")
                return f"{base}/sign-in/email"

        for url, r in api.items():
            if any(w in url.lower() for w in ("login", "signin", "authenticate")) \
                    and "POST" in r["methods"]:
                return url
        for url, r in api.items():
            if "POST" in r["methods"] and "password" in files.get(r["file"], ""):
                return url
        return ""

    def demo_credentials(self) -> list:
        """(email, password) pairs the app claims will work."""
        creds = []
        for a in (self.arch.plan or {}).get("demo_accounts", []) or []:
            if a.get("email") and a.get("password"):
                creds.append((a["email"], a["password"]))
        if creds:
            return creds

        section = re.search(r"^#+ Demo Accounts\s*$(.*?)(?=^#+ |\Z)",
                            self.plan_text(), re.M | re.S)
        if section:
            for line in section.group(1).splitlines():

                cells = [c.strip().strip("`*") for c in line.split("|")]
                if len(cells) > 2:
                    for i, c in enumerate(cells[:-1]):
                        if re.fullmatch(r"[\w.+-]+@[\w.-]+\.\w+", c):
                            nxt = cells[i + 1]
                            if re.fullmatch(r"[A-Za-z0-9!@#$%^&*_-]{6,}", nxt):
                                creds.append((c, nxt))
                            break
                    continue
                m = re.search(r"([\w.+-]+@[\w.-]+)\D+?([A-Za-z0-9!@#$%^&*_-]{6,})\s*$",
                              line)
                if m:
                    creds.append((m.group(1), m.group(2)))
        if creds:
            return creds

        creds = self._credentials_from_seed()
        if creds:
            return creds

        for path, content in self.code_files().items():
            if "login" not in path.lower() and "signin" not in path.lower():
                continue

            visible = re.sub(r"""placeholder\s*=\s*["'{][^"'}]*["'}]""", "",
                             content)
            emails = re.findall(r"[\w.+-]+@[\w.-]+\.\w+", visible)

            pw = re.search(
                r"[Pp]assword\b[^:<\n]{0,40}:\s*(?:<[^>]*>\s*)*"
                r"([A-Za-z0-9!@#$%^&*_-]{6,})",
                content)
            if emails and pw:
                creds = [(e, pw.group(1)) for e in dict.fromkeys(emails)]
                break
        return creds

    SEED_EMAIL_RE = re.compile(r"""email\s*:\s*['"]([\w.+-]+@[\w.-]+\.\w+)['"]""")
    SEED_PW_FIELD_RE = re.compile(
        r"""(?:password\s*:\s*['"]([^'"]{4,})['"]"""
        r"""|hashSync\s*\(\s*['"]([^'"]{4,})['"])""")
    SEED_PW_CONST_RE = re.compile(
        r"""\b\w*PASSWORD\w*\s*=\s*['"]([^'"]{4,})['"]""")

    SEED_INDIRECT_HASH_RE = re.compile(r"""hashSync\s*\(\s*[^'"\s)]""")

    def _credentials_from_seed(self) -> list:
        """Read the demo accounts out of `lib/seed.js`."""
        seed = "\n".join(c for p, c in sorted(self.code_files().items())
                         if "seed" in p.lower())
        if not seed:
            return []
        hits = list(self.SEED_EMAIL_RE.finditer(seed))
        if not hits:
            return []

        if self.SEED_INDIRECT_HASH_RE.search(seed):
            return []

        creds = []
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(seed)
            pw = self.SEED_PW_FIELD_RE.search(seed, m.end(), end)
            if not pw:
                creds = []
                break
            creds.append((m.group(1), pw.group(1) or pw.group(2)))
        if creds:
            return creds

        literals = {(a or b) for a, b in self.SEED_PW_FIELD_RE.findall(seed)}
        literals |= set(self.SEED_PW_CONST_RE.findall(seed))
        if len(literals) == 1:
            shared = literals.pop()
            return [(m.group(1), shared) for m in hits]
        return []

    def _announce_credentials(self, report: AnalyzerReport = None) -> list:
        """Hand the demo accounts to AgentForge's UI."""
        creds = self.demo_credentials()
        if not creds:
            return []
        roles = {a.get("email"): a.get("role", "")
                 for a in (self.arch.plan or {}).get("demo_accounts", []) or []}
        by_email = {c.get("email"): c.get("status")
                    for c in (report.credentials.get("checked") if report else []) or []}
        accounts = [{"email": e, "password": p, "role": roles.get(e, ""),
                     "status": by_email.get(e)}
                    for e, p in creds]
        source = ("plan" if (self.arch.plan or {}).get("demo_accounts")
                  else "project")
        self._fire("on_creds", accounts, source,
                   (report.credentials.get("ok") if report else None))
        return accounts

    def verify_credentials(self, report: AnalyzerReport) -> None:
        """POST the demo credentials at the running app."""

        if any(f.code == "ROUTE_ERROR" for f in report.findings):
            report.credentials = {"checked": [], "ok": None,
                                  "reason": "pages are failing; login cannot "
                                            "be judged until they serve"}
            self._log("INFO", "   ⏭  Skipping the login check — pages are "
                              "still failing")
            return

        creds = self.demo_credentials()
        if not creds:
            report.credentials = {"checked": [], "ok": None,
                                  "reason": "no demo accounts"}
            return
        endpoint = self.find_login_endpoint()
        if not endpoint:
            report.credentials = {"checked": [], "ok": None,
                                  "reason": "no login endpoint"}
            report.findings.append(Finding(
                "major", "NO_LOGIN_ENDPOINT",
                "the plan lists demo accounts but no route handler verifies a "
                "password"))
            return

        url = self.base_url + endpoint
        checked, failures, unreachable = [], [], False
        for email, password in creds[:3]:
            status, body = self._post_json(url, {"email": email,
                                                 "password": password})
            if status == 400 and "username" in (body or "").lower():
                status, body = self._post_json(url, {"username": email,
                                                     "password": password})
            checked.append({"email": email, "status": status})
            if status is None:
                unreachable = True
                break
            self._fire("on_test", "pass" if status < 400 else "fail",
                       f"Login {email}", f"HTTP {status}")
            if status in (401, 403):
                failures.append((email, password, status, body))
            elif status >= 400:
                report.findings.append(Finding(
                    "major", "LOGIN_ERROR",
                    f"POST {endpoint} returned {status} for {email}: "
                    f"{(body or '')[:160]}"))

        if unreachable:
            report.credentials = {"checked": checked, "ok": None,
                                  "reason": "endpoint unreachable"}
            return

        report.credentials = {"endpoint": endpoint, "checked": checked,
                              "ok": not failures}
        for email, password, status, _ in failures:
            report.findings.append(Finding(
                "blocker", "BAD_CREDENTIALS",
                f"POST {endpoint} with the demo credentials the app advertises "
                f"({email} / {password}) returned {status} — bcrypt.compare "
                f"fails, so the seeded passwordHash does not correspond to "
                f"this password",
                path=self.enumerate_routes().get(endpoint, {}).get("file", ""),
                fix=f"seed passwordHash with bcrypt.hashSync('{password}', 10) "
                    f"for {email}. Do NOT display the credentials in the app — "
                    f"they are shown to the developer outside it"))
