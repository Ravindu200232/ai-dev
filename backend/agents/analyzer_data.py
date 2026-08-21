"""Credential, seed, session, props and endpoint consistency checks."""
from .analyzer_common import *


class AnalyzerDataMixin:
    DEMO_HEADING_RE = re.compile(r"[Dd]emo\s+[Aa]ccounts?|[Tt]est\s+[Cc]redentials?")
    EMAIL_LITERAL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")

    PREFILL_RE = re.compile(r"""(?:defaultValue|useState)\s*[=(]\s*['"][\w.+-]+@""")
    PLACEHOLDER_ATTR_RE = re.compile(r"""placeholder\s*=\s*["'{][^"'}]*["'}]""")

    def credentials_exposed(self) -> list:
        """Demo credentials rendered inside the generated app.

        Every check here needs an account to leak. An app with no sign-in
        seeds none, so an email in a contact form is just an email — and
        calling it a credential leak has already cost one whole feature.
        """
        out = []
        creds = self.demo_credentials()
        if not creds:
            return out
        passwords = {p for _e, p in creds if p}
        emails = {e.lower() for e, _p in creds if e}
        files = self.code_files()
        for path, content in sorted(files.items()):
            if not path.startswith(("app/", "components/")):
                continue
            if "seed" in path.lower():
                continue
            vis = self.PLACEHOLDER_ATTR_RE.sub("", content)
            why = []
            if any(p in vis for p in passwords):
                why.append("a seeded password appears in the markup")
            for m in self.DEMO_HEADING_RE.finditer(vis):
                if self.EMAIL_LITERAL_RE.search(vis[m.start():m.start() + 400]):
                    why.append("a 'Demo Accounts' panel lists real addresses")
                    break

            if re.search(r"""DEMO_ACCOUNTS|demoAccounts""", vis):
                why.append("it renders a DEMO_ACCOUNTS list")
            prefilled = self.PREFILL_RE.search(vis)
            # Only a SEEDED address is a leak. Any other default is the app's
            # own copy, and removing it is not this check's business.
            if prefilled and any(e in vis.lower() for e in emails):
                why.append("the sign-in form is pre-filled with a seeded "
                           "account's address")
            if why:
                out.append(Finding(
                    "major", "CREDS_IN_UI",
                    f"demo credentials are visible in the app — {why[0]}",
                    path=path,
                    fix="remove the demo-account panel and any pre-filled "
                        "values; the accounts stay seeded and AgentForge shows "
                        "them to the developer outside the app"))
        return out

    SEED_COUNT_RE = re.compile(r"Array\.from\s*\(\s*\{\s*length\s*:\s*(\d+)"
                               r"|for\s*\(\s*(?:let|var)\s+\w+\s*=\s*0\s*;"
                               r"\s*\w+\s*<\s*(\d+)")

    def seed_volume(self) -> list:
        """More demo rows than anyone asked for."""
        if "Sample data: requested" in self.plan_text():
            return []
        out = []
        for path, content in sorted(self.code_files().items()):
            if "seed" not in path.lower() or not path.startswith("lib/"):
                continue
            counts = []
            for m in re.finditer(r"const\s+(\w+)\s*=\s*\[", content):
                n = self._count_top_level_objects(content, m.end() - 1)
                if n >= 5:
                    counts.append((m.group(1), n))
            for m in self.SEED_COUNT_RE.finditer(content):
                n = int(m.group(1) or m.group(2))
                if n >= 5:
                    counts.append(("generated", n))

            counts = [(name, n) for name, n in counts
                      if "user" not in (name or "").lower()]
            if counts:
                worst = max(counts, key=lambda c: c[1])
                out.append(Finding(
                    "minor", "SEED_VOLUME",
                    f"seeds {worst[1]} `{worst[0]}` documents; the plan did not "
                    f"ask for sample data, so fewer than 5 is enough to prove "
                    f"the screens work",
                    path=path,
                    fix="cut the demo rows down unless the idea asked for "
                        "sample data"))
        return out

    @staticmethod
    def _count_top_level_objects(src: str, open_bracket: int) -> int:
        """Objects directly inside the array literal starting at `[`."""
        depth, n, i = 0, 0, open_bracket
        while i < len(src):
            c = src[i]
            if c in "[{":
                depth += 1
                if depth == 2 and c == "{":
                    n += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    return n
            i += 1
        return n

    COOKIE_SET_RE = re.compile(r"\.set\(\s*(?:['\"]([^'\"]+)['\"]|([A-Z_][A-Z0-9_]*))")
    COOKIE_GET_RE = re.compile(
        r"\.(?:get|delete)\(\s*(?:['\"]([^'\"]+)['\"]|([A-Z_][A-Z0-9_]*))")
    CONST_STR_RE = re.compile(r"\bconst\s+([A-Z_][A-Z0-9_]*)\s*=\s*['\"]([^'\"]+)['\"]")

    SESSIONISH_RE = re.compile(r"session|token|auth|jwt|user", re.I)

    def session_cookie_mismatch(self) -> list:
        """The session cookie is written under one name and read under another."""
        files = self.code_files()
        written, read_at = set(), {}
        for path, content in sorted(files.items()):
            consts = dict(self.CONST_STR_RE.findall(content))
            if "cookies" not in content and "cookieStore" not in content:
                continue
            for lit, ident in self.COOKIE_SET_RE.findall(content):
                name = lit or consts.get(ident)
                if name:
                    written.add(name)
            for lit, ident in self.COOKIE_GET_RE.findall(content):
                name = lit or consts.get(ident)
                if name and self.SESSIONISH_RE.search(name):
                    read_at.setdefault(name, []).append(path)

        out = []

        login = self.find_login_endpoint()
        login_file = (f"app{login}/route.js" if login else "")
        reader = "lib/auth.js" if "lib/auth.js" in files else ""
        if login_file in files and reader:
            def names(src, rx):
                consts = dict(self.CONST_STR_RE.findall(files[src]))
                got = set()
                for lit, ident in rx.findall(files[src]):
                    n = lit or consts.get(ident)
                    if n and self.SESSIONISH_RE.search(n):
                        got.add(n)
                return got

            sets = names(login_file, self.COOKIE_SET_RE)
            gets = names(reader, self.COOKIE_GET_RE)
            if sets and gets and not (sets & gets):
                out.append(Finding(
                    "blocker", "SESSION_COOKIE_MISMATCH",
                    f"sets the cookie `{sorted(sets)[0]}`, but {reader} — which "
                    f"getSessionUser(), /api/auth/me and every guarded page go "
                    f"through — reads `{sorted(gets)[0]}`. Login returns 200 "
                    f"and really does set a cookie, the redirect happens, and "
                    f"then the very next request finds no session and sends the "
                    f"user straight back to the login page",
                    path=login_file,
                    fix=f"delete the inline cookie write in {login_file} and "
                        f"call the session helper {reader} already exports, so "
                        f"one constant is the only cookie name in the app",
                    extra=[reader]))

        covered = {p for f in out for p in [f.path] + list(f.extra)}
        for name, paths in sorted(read_at.items()):
            if name in written or set(paths) <= covered:
                continue
            where = ", ".join(sorted(set(paths))[:3])
            out.append(Finding(
                "blocker", "SESSION_COOKIE_MISMATCH",
                f"reads the cookie `{name}`, which nothing in this app ever "
                f"sets. "
                + (f"The cookie that IS written is "
                   f"`{sorted(written)[0]}`. " if len(written) == 1 else
                   f"The cookies that ARE written: "
                   f"{', '.join(sorted(written))}. " if written else
                   "No cookie is written anywhere. ")
                + "Login will return 200 and set a cookie, the redirect will "
                  "happen, and the very next request will find no session and "
                  "send the user straight back to the login page",
                path=sorted(set(paths))[0],
                fix=f"use ONE cookie name everywhere: export a single constant "
                    f"from lib/auth.js and have the login route, the session "
                    f"reader and the logout route all import it. Read at: "
                    f"{where}",
                extra=sorted(set(paths))))
        return out

    UNIQUE_INDEX_RE = re.compile(
        r"createIndex\s*\([^)]*?\{\s*unique\s*:\s*true", re.S)

    def unique_index_in_seed(self) -> list:
        """A unique index built by the seed, over data that outlives the code."""
        out = []
        for path, content in sorted(self.code_files().items()):
            if "seed" not in path.lower():
                continue
            for m in self.UNIQUE_INDEX_RE.finditer(content):
                field = re.search(r"createIndex\s*\(\s*\{\s*([\w.]+)",
                                  content[m.start():m.start() + 120])
                name = field.group(1) if field else "?"
                line = content[:m.start()].count("\n") + 1
                out.append(Finding(
                    "blocker", "UNIQUE_INDEX_IN_SEED",
                    f"builds a unique index on `{name}` at line {line}. The "
                    f"database survives regeneration, so this will meet "
                    f"documents written before `{name}` existed; two nulls "
                    f"make the build throw E11000 inside ensureSeeded(), and "
                    f"every page that awaits it returns 500",
                    path=path,
                    fix=f"drop the unique index on `{name}`, or at minimum move "
                        f"every createIndex inside the same "
                        f"`if (await col.countDocuments() > 0) return` guard as "
                        f"the insert and remove `unique: true`"))
                break
        return out

    COMPONENT_PROPS_RE = re.compile(
        r"export\s+default\s+(?:async\s+)?function\s+(\w+)\s*\(\s*\{([^}]*)\}"
        r"|export\s+default\s+function\s*\(\s*\{([^}]*)\}")
    JSX_OPEN_RE = re.compile(r"<([A-Z]\w*)(?=[\s/>])")
    JSX_PROP_RE = re.compile(r"(\w+)\s*=\s*[{\"']")

    @staticmethod
    def _jsx_attrs(src: str, start: int):
        """The attribute text of the JSX tag opening at `start`, or None."""
        i, depth, quote = start, 0, ""
        while i < len(src):
            c = src[i]
            if quote:
                if c == "\\":
                    i += 2
                    continue
                if c == quote:
                    quote = ""
            elif c in "\"'`":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == ">" and depth == 0:
                return src[start:i]
            elif c == "<" and depth == 0 and i > start:
                return None
            i += 1
        return None

    @staticmethod
    def _prop_is_required(body: str, name: str) -> bool:
        """True when the component would throw if this prop were undefined."""
        n = re.escape(name)
        if re.search(rf"\b{n}\s*(?:&&|\?\.|\?[^.]|\|\||\?\?)", body):
            return False
        if re.search(rf"\bif\s*\(\s*!?\s*{n}\b", body):
            return False

        return bool(re.search(rf"\b{n}\s*(?:\.|\(|\[)", body)
                    or re.search(rf"\{{\s*\.\.\.\s*{n}\b", body))

    def prop_contract_breaks(self) -> list:
        """A component rendered without the prop it destructures."""
        files = self.code_files()

        contracts = {}
        for path, content in files.items():
            if not path.startswith("components/"):
                continue
            m = self.COMPONENT_PROPS_RE.search(content)
            if not m:
                continue
            body = m.group(2) or m.group(3) or ""
            if "..." in body:
                continue
            names = []
            for part in body.split(","):
                part = part.strip()
                if not part or "=" in part or ":" in part:
                    continue
                if not re.fullmatch(r"\w+", part) or part == "children":
                    continue
                if self._prop_is_required(content, part):
                    names.append(part)
            if names:
                contracts[path] = set(names)

        out = []
        for path, content in sorted(files.items()):

            local = {}
            for st in parse_imports(content):
                if not st.default or not st.spec.startswith(("./", "../", "@/")):
                    continue
                target = resolve_local(path, st.spec, files)
                if target:
                    local[st.default] = target

            for m in self.JSX_OPEN_RE.finditer(content):
                comp = m.group(1)
                src = local.get(comp)
                if src is None or src not in contracts or src == path:
                    continue
                required = contracts[src]
                attrs = self._jsx_attrs(content, m.end())
                if attrs is None:
                    continue
                if re.search(r"\{\s*\.\.\.", attrs):
                    continue
                given = set(self.JSX_PROP_RE.findall(attrs))
                missing = required - given
                if not missing:
                    continue
                line = content[:m.start()].count("\n") + 1
                out.append(Finding(
                    "blocker", "PROP_CONTRACT",
                    f"line {line} renders <{comp}> without "
                    f"{', '.join(sorted(missing))}, which {src} destructures "
                    f"and uses. It will be undefined at runtime and the first "
                    f"property read on it throws — a 500 on a page that "
                    f"compiles cleanly"
                    + (f". Given instead: {', '.join(sorted(given))}"
                       if given else ""),
                    path=path,
                    fix=f"pass {', '.join(sorted(missing))} to <{comp}>, or "
                        f"change {src} to accept what this call site actually "
                        f"has",
                    extra=[src]))
        return out

    def dead_endpoints(self, routes: dict = None) -> list:
        """`fetch('/api/…')` calls that no route handler serves."""
        routes = self.enumerate_routes() if routes is None else routes
        apis = [u for u, r in routes.items() if r["kind"] == "api"]
        dead = set()
        for path, content in self.code_files().items():
            for raw in FETCH_URL_RE.findall(content):
                url = raw.split("?")[0].rstrip("/") or "/"
                if not self._route_matches(url, apis):
                    dead.add(url)
        return sorted(dead)
