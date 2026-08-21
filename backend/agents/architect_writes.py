"""Atomic file writes, role coverage and planned route tracking."""
from .architect_common import *


ROUTE_METHOD_RE = re.compile(
    r"^[^\S\n]*export\s+(?:async\s+)?(?:function\s+|(?:const|let|var)\s+)"
    r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", re.M)


def _route_methods(text: str) -> set:
    """The HTTP verbs an App Router route file exports."""
    return set(ROUTE_METHOD_RE.findall(text or ""))


class ArchitectWriteMixin:
    def write_file(self, rel: str, content: str) -> bool:
        try:
            fp = self._safe_path(rel)
            key0 = str(fp.relative_to(self.project_dir)).replace("\\", "/")
            if key0 == "package.json" and not self._scaffolding:
                content = self._merge_package_json(content)

            if self.stack == "next" and key0 not in self.files:
                canon = self.canonical_path(key0)
                if canon != key0:
                    self._log("INFO", f"   ↪ {key0} → {canon}")
                    fp = self._safe_path(canon)
                    key0 = canon

                twin = (key0[:-4] + ".js" if key0.endswith(".jsx")
                        else key0[:-3] + ".jsx")
                tp = self.project_dir / twin
                if tp.is_file():
                    try:
                        tp.unlink()
                        self.files.pop(twin, None)
                        self._log("WARN", f"   🗑 removed the shadowed {twin}")
                    except Exception as e:
                        self._log("WARN", f"   could not remove {twin}: {e}")

            if (self.stack == "next" and not self._scaffolding
                    and key0.startswith("app/api/auth/")
                    and not key0.startswith("app/api/auth/[")
                    and "app/api/auth/[...all]/route.js" in self.files):
                return self._refuse(
                    key0, "would shadow the generated /api/auth handler — auth "
                          "is already built, use signIn.email / getSessionUser "
                          "instead")

            # The rule above stopped the handler being shadowed and left.
            if (self.stack == "next" and not self._scaffolding
                    and key0 in ("lib/auth.js", "lib/auth-client.js")
                    and key0 not in set(getattr(self, "_e2e_privileged_paths", set()) or set())
                    and "app/api/auth/[...all]/route.js" in self.files):
                return self._refuse(
                    key0, "is generated and already correct — it holds the real "
                          "Better Auth instance and rewriting it (a stub, a "
                          "mock, a 'simplified' version) leaves `auth.handler` "
                          "undefined and 500s every /api/auth/* request. Import "
                          "from it instead: `auth` and `getSessionUser` from "
                          "`@/lib/auth` in server code, `signIn` / `signUp` / "
                          "`useSession` from `@/lib/auth-client` in a client "
                          "component. The sign-up role is not yours to change "
                          f"either: this app's sign-up creates a "
                          f"`{self._signup_role()}`, set from the plan, and it "
                          "is right. If somebody lands on the wrong page after "
                          "signing in, the redirect is what is wrong — read "
                          "`user.role` and send each role to its own page — or "
                          "the test signed in as the wrong person, which is not "
                          "something to fix in the app at all.")

            if (self.stack == "next" and not self._scaffolding
                    and key0.startswith("app/") and key0.endswith((".jsx", ".js"))
                    and re.search(r"^\s*['\"]use client['\"]", content, re.M)
                    and re.search(r"from\s+['\"]@/lib/auth['\"]", content)):
                return self._refuse(key0, f"is a client component and imports "
                          f"`@/lib/auth`, which cannot run in a browser — it "
                          f"brings the MongoDB driver with it and the build "
                          f"fails on 'tls'. Read the session in a SERVER page "
                          f"and pass what it needs to a client child, or use "
                          f"`useSession()` from `@/lib/auth-client`.")

            if (self.stack == "next" and not self._scaffolding
                    and key0.startswith("app/")
                    and re.search(r"""['"]/api/auth/me['"]""", content)):
                return self._refuse(key0, f"fetches `/api/auth/me`, which Better "
                          f"Auth does not serve — it answers 404 and the page "
                          f"sends everyone to /login. The session endpoint is "
                          f"`/api/auth/get-session`, and from a client "
                          f"component `useSession()` is better than fetching "
                          f"at all.")

            if (self.stack == "next" and not self._scaffolding
                    and key0 in ("middleware.js", "middleware.ts")):
                if any((self.project_dir / f"proxy{e}").is_file()
                       or f"proxy{e}" in self.files for e in (".js", ".ts")):
                    return self._refuse(key0, f"— Next 16 uses proxy.js, and "
                              f"having both is a startup error, not a "
                              f"compatibility shim. proxy.js is already there; "
                              f"put the logic in that.")
            if (self.stack == "next" and key0 in ("proxy.js", "proxy.ts")):
                for ext in (".js", ".ts"):
                    mw = self.project_dir / f"middleware{ext}"
                    if mw.is_file():
                        try:
                            mw.unlink()
                            self.files.pop(f"middleware{ext}", None)
                            self._log("WARN", f"   🗑 removed middleware{ext} — "
                                              f"Next 16 refuses to start with "
                                              f"both it and proxy.js")
                        except Exception as e:
                            self._log("WARN", f"   could not remove "
                                              f"middleware{ext}: {e}")

            if (self.stack == "next" and not self._scaffolding
                    and key0.startswith("app/")):
                folder = key0.rsplit("/", 1)[0]
                name = key0.rsplit("/", 1)[-1]
                twins = {"route.js": ("page.jsx", "page.js"),
                         "route.jsx": ("page.jsx", "page.js"),
                         "page.jsx": ("route.js", "route.ts"),
                         "page.js": ("route.js", "route.ts")}.get(name, ())
                clash = next((f"{folder}/{t}" for t in twins
                              if f"{folder}/{t}" in self.files
                              or (self.project_dir / folder / t).is_file()), "")
                if clash:
                    kind = "route handler" if name.startswith("route") else "page"
                    return self._refuse(key0, f"cannot be a {kind}: {clash} is "
                              f"already there, and Next refuses a segment that "
                              f"is both — it fails to compile and the URL 404s. "
                              f"Put the handler one level down, at "
                              f"{folder}/api/route.js, or use a Server Action.")

            if (self.stack == "next" and not self._scaffolding
                    and self.canonical_path(key0) in self.NEXT_PROTECTED
                    and key0 not in set(getattr(self, "_e2e_privileged_paths", set()) or set())):

                return self._refuse(key0, f"is generated by AgentForge and "
                                  f"cannot be edited — fix the file that "
                                  f"actually has the problem")
            content = _strip_fence(content).rstrip() + "\n"
            content = _fix_doubled_tags(content)

            if (self.stack == "next" and not self._scaffolding
                    and key0.startswith("app/api/")
                    and key0.endswith(("/route.js", "/route.jsx"))):
                had = _route_methods(self.files.get(key0, ""))
                now = _route_methods(content)
                lost = sorted(had - now)
                if lost:
                    return self._refuse(
                        key0,
                        f"drops the {', '.join(lost)} handler(s) it already "
                        f"exported. A route file is the whole endpoint, not "
                        f"the one verb being worked on — anything still "
                        f"calling {lost[0]} gets 405 and no check catches it. "
                        f"Write the file with every handler it had "
                        f"({', '.join(sorted(had))}) plus whatever is being "
                        f"added.")

            if (self.stack == "next" and key0 in ("app/layout.jsx", "app/layout.js")
                    and "globals.css" not in content):
                content = "import './globals.css'\n" + content.lstrip("\n")
                self._log("WARN", "   🔧 app/layout — put back "
                                  "`import './globals.css'`; without it the "
                                  "whole app renders unstyled and every check "
                                  "still passes")

            if (self.stack == "next" and key0 == "app/globals.css"
                    and "@tailwind" not in content and "@import" not in content):
                content = ("@tailwind base;\n@tailwind components;\n"
                           "@tailwind utilities;\n\n") + content.lstrip("\n")
                self._log("WARN", "   🔧 app/globals.css — put back the "
                                  "@tailwind directives; without them the "
                                  "whole app renders unstyled and every check "
                                  "still passes")

            if self.stack == "next":
                content = self._drop_layout_duplicates(key0, content)

            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            key = str(fp.relative_to(self.project_dir)).replace("\\", "/")
            self.files[key] = content
            # A rewrite gets a fresh chance at the per-turn gate.
            getattr(self, "_invalid_generated", set()).discard(key)
            if hasattr(self, "_write_history"):
                self._write_history.append(key)

            self.write_seq = getattr(self, "write_seq", 0) + 1
            size = (f"{len(content) / 1024:.1f}KB" if len(content) >= 1024
                    else f"{len(content)}B")
            self._fire("on_file_written", key, size, content)
            self._log("INFO", f"   📝 {key}  ({size})")
            self._drop_tests_for(key, content)
            return True
        except Exception as e:
            self._log("ERROR", f"   ❌ write failed {rel}: {e}")
            return False

    PUBLIC_AREAS = frozenset({
        "login", "signup", "register", "auth", "about", "contact",
        "browse", "search", "list", "new", "edit", "manage", "settings",
        "profile", "history", "reports"})

    def _roles_without_areas(self, plan):
        """`(roles, areas)` when a plan has fewer signed-in areas than roles."""
        roles = {a.get("role", "").lower()
                 for a in (plan.get("demo_accounts") or [])
                 if isinstance(a, dict) and a.get("role")}
        if len(roles) < 3:
            return None
        pages = [f if isinstance(f, str) else (f or {}).get("path", "")
                 for ph in plan.get("phases", []) for f in ph.get("files", [])]
        areas = {p[4:].split("/")[0] for p in pages
                 if p.endswith(("page.jsx", "page.js"))
                 and p.startswith("app/") and p[4:].count("/") >= 1}
        areas -= self.PUBLIC_AREAS
        return (roles, areas) if len(areas) < len(roles) - 1 else None

    def _uncovered(self, plan: dict) -> list:
        """Requirement ids in the brief that no task's `covers` carries."""
        known = getattr(self, "_known_fr", None) or set()
        if not known:
            return []
        placed = set()
        for phase in (plan or {}).get("phases") or []:
            for c in phase.get("covers") or []:
                placed.add(str(c).strip().upper())
        left = [fr for fr in known if fr.upper() not in placed]
        return sorted(left, key=lambda s: int(re.sub(r"\D", "", s) or 0))

    _ROUTE_TOKEN = re.compile(r"(?<![\w\]])(/(?:[a-z0-9][a-z0-9\[\]._-]*)?"
                              r"(?:/[a-z0-9\[\]._-]+)*)")
    _ARROW = re.compile(r"→|->|=>|➔|⟶|\\(?:right|long)?arrow")
    _GENERATED_ROUTES = ("/api/auth", "/api/health")

    def _routes_promised(self, markdown: str) -> list:
        """Every route the plan DECLARES, from the two places it declares them."""
        out, seen = [], set()
        for line in (markdown or "").splitlines():
            # Backticks out first.
            s = line.replace("`", " ").strip()
            if not s or s.startswith("#"):
                continue
            found = []
            if s.startswith("|"):
                cells = [c.strip().strip("*_ ") for c in s.strip("|").split("|")]
                if cells and cells[0].startswith("/"):
                    m = self._ROUTE_TOKEN.match(cells[0])
                    if m:
                        found.append(m.group(1))
            if self._ARROW.search(s):
                found += self._ROUTE_TOKEN.findall(s)
            for route in found:
                route = route.rstrip("/.,;:)") or "/"
                low = route.lower()
                if (low in seen or low.startswith(self._GENERATED_ROUTES)
                        or re.search(r"\.(jsx?|tsx?|css|png|jpe?g|svg|ico|json|md)$", low)
                        or low.startswith(("/node_modules", "/public", "/app/",
                                           "/lib/", "/components/", "/tests/"))):
                    continue
                seen.add(low)
                out.append(route)
        return out

    @staticmethod
    def _route_of(path: str) -> str:
        """`app/items/[id]/page.jsx` → `/items/[id]`; `app/page.jsx` → `/`."""
        p = str(path or "").replace("\\", "/").strip()
        if not p.startswith("app/"):
            return ""
        if not re.search(r"/(page|route)\.(jsx?|tsx?)$", p):
            return ""
        parts = p.split("/")[1:-1]  # Drop 'app' and the file
        parts = [s for s in parts if not (s.startswith("(") and s.endswith(")"))]
        return "/" + "/".join(parts) if parts else "/"

    @staticmethod
    def _route_key(route: str) -> tuple:
        """A route as comparable segments, with `[id]` as a wildcard."""
        segs = [s for s in str(route or "").strip().lower().split("/") if s]
        return tuple("*" if s.startswith("[") else s for s in segs)

    def _unbuilt_routes(self, plan: dict, markdown: str) -> list:
        """Routes the plan promises that no planned file will serve."""
        planned = set()
        for phase in (plan or {}).get("phases") or []:
            for f in phase.get("files") or []:
                path = f if isinstance(f, str) else (f or {}).get("path", "")
                route = self._route_of(path)
                if route:
                    planned.add(self._route_key(route))
        if not planned:
            return []

        missing = []
        for route in self._routes_promised(markdown):
            key = self._route_key(route)
            if any(len(key) == len(p) and all(a == b or b == "*"
                                              for a, b in zip(key, p))
                   for p in planned):
                continue
            missing.append(route)
        return missing[:12]
