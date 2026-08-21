"""The security pass: what a generated app can be proved to have got wrong."""
import json
import os
import logging
import re
from pathlib import Path

from agents.analyzer import Finding

log = logging.getLogger("security")


PUBLIC_ROUTES = ("app/api/health/", "app/api/auth/")


MUTATING = ("POST", "PUT", "PATCH", "DELETE")
ALWAYS_GUARD = ("PUT", "PATCH", "DELETE")

MUTATES_EXISTING_RE = re.compile(
    r"\.(updateOne|updateMany|deleteOne|deleteMany|bulkWrite|"
    r"findOneAndUpdate|findOneAndDelete|replaceOne)\s*\(")
HANDLER_RE = re.compile(
    r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD)\b"
    r"|export\s+const\s+(GET|POST|PUT|PATCH|DELETE|HEAD)\s*=")


AUTH_SIGNALS = re.compile(
    r"getSessionUser|getServerSession|auth\.api\.getSession|\bauth\s*\(\)"
    r"|\brequire[A-Z]\w*\s*\("
    r"|\bif\s*\(\s*!\s*(?:session|user)\b"
    r"|\b(?:session|user)\s*(?:\?\s*\.|\.)\s*role\b"
    r"|status:\s*(?:401|403)\b"
    r"|\b(?:401|403)\s*\}")


PUBLIC_SECRET_RE = re.compile(
    r"NEXT_PUBLIC_\w*(SECRET|PRIVATE|PASSWORD|_KEY|APIKEY|TOKEN|CREDENTIAL)\w*",
    re.I)


RAW_FILTER_RE = re.compile(
    r"\.(?:find|findOne|deleteOne|deleteMany|updateOne|updateMany|countDocuments)"
    r"\s*\(\s*(body|payload|await\s+request\.json\(\)|req\.body)\s*[,)]")
WHERE_RE = re.compile(r"\$where\s*:\s*[`\"'].*?\$\{", re.S)


PLAIN_PASSWORD_RE = re.compile(
    r"password\s*===\s*|===\s*\w*\.password\b|password:\s*(?:body|data)\.password",
    re.I)
HASH_RE = re.compile(r"bcrypt|argon2|scrypt|pbkdf2|createHash|hashPassword"
                     r"|better-auth", re.I)


UNSAFE_HTML_RE = re.compile(
    r"dangerouslySetInnerHTML\s*=\s*\{\{\s*__html:\s*(?!['\"`])([^}]+)\}\}")

SKIP_DIRS = ("node_modules", ".next", "tests", ".agentforge", "public", ".git")


class SecurityAgent:
    """Reads a finished project and reports what it can prove is unsafe."""

    def __init__(self, project_dir, *, callbacks=None, cmd=None):
        self.project_dir = Path(project_dir)
        self.cb = callbacks or {}
        self.cmd = cmd

    def _fire(self, name, *a):
        fn = self.cb.get(name)
        if fn and callable(fn):
            try:
                fn(*a)
            except Exception as e:
                log.warning(f"callback {name} failed: {e}")

    def _log(self, lvl, txt):
        self._fire("on_log", lvl, txt)
        log.info(txt)

    def _plan_contract(self):
        """Read the finished app contract when security policy depends on actors."""
        fp = self.project_dir / ".agentforge" / "plan.json"
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _public_write_app(self) -> bool:
        """True only when the product contract has no authenticated actor."""
        plan = self._plan_contract()
        if not plan:
            return False
        if plan.get("demo_accounts"):
            return False
        public = {"", "public", "signed-out", "signed out", "anonymous", "visitor",
                  "none", "nobody", "logged-out", "logged out"}
        actors = []
        for key in ("capabilities", "workflows"):
            for row in plan.get(key) or []:
                if isinstance(row, dict):
                    actors.append(str(row.get("who") or row.get("role") or
                                      row.get("actor") or "").strip().lower())
        return bool(actors) and all(actor in public for actor in actors)

    def _sources(self):
        """Every source file in the project, as `(relative path, body)`."""
        root = self.project_dir
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in sorted(filenames):
                if not name.endswith((".js", ".jsx", ".mjs")):
                    continue
                f = Path(dirpath) / name
                rel = str(f.relative_to(root)).replace("\\", "/")
                try:
                    yield rel, f.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    log.debug(f"security: cannot read {rel}: {e}")

    def unguarded_routes(self) -> list:
        """A write handler that never establishes who is calling."""
        out = []
        public_write_app = self._public_write_app()
        for rel, body in self._sources():
            if not rel.startswith("app/api/") or not rel.endswith("route.js"):
                continue
            if any(rel.startswith(p) for p in PUBLIC_ROUTES):
                continue
            verbs = {m.group(1) or m.group(2) for m in HANDLER_RE.finditer(body)}
            writes = sorted(verbs & set(MUTATING))
            if not writes or AUTH_SIGNALS.search(body):
                continue

            if not (set(writes) & set(ALWAYS_GUARD)
                    or MUTATES_EXISTING_RE.search(body)):
                continue
            public_write = public_write_app
            out.append(Finding(
                severity="minor" if public_write else "blocker",
                code="PUBLIC_WRITE_ROUTE" if public_write else "UNGUARDED_ROUTE",
                path=rel,
                message=((f"{', '.join(writes)} intentionally changes records "
                          "without a session because every actor in the product "
                          "contract is public") if public_write else
                         (f"{', '.join(writes)} changes records that already "
                          f"exist and never checks who is calling — anyone who "
                          f"knows the URL can use it")),
                fix=(("Keep the route public; validate input and record ids, but "
                      "do not invent authentication the product does not require.")
                     if public_write else
                     ("Read the session at the top of each write handler and "
                      "return 401 with no session, 403 for the wrong role, "
                      "before anything is written. Use the same helper the "
                      "app's other guarded routes use — do not invent a new "
                      "one, and do not guard the GET if it is meant to be "
                      "public."))))
        return out

    def unguarded_pages(self) -> list:
        """A page under a role-gated section that checks nothing itself."""
        pages, guarded = {}, set()

        guard_dirs = []
        for rel, body in self._sources():
            if not rel.endswith(("layout.js", "layout.jsx")):
                continue
            if AUTH_SIGNALS.search(body) or "getSessionUser" in body:
                guard_dirs.append(rel.rsplit("/", 1)[0] + "/")

        for rel, body in self._sources():
            if not rel.startswith("app/") or "/page.js" not in rel + "/":
                continue
            if not rel.endswith(("page.js", "page.jsx")):
                continue
            section = rel[len("app/"):].rsplit("/", 1)[0].split("/")[0]
            pages[rel] = (section, body)
            if AUTH_SIGNALS.search(body) or "getSessionUser" in body:
                guarded.add(section)

        out = []
        for rel, (section, body) in sorted(pages.items()):
            if section not in guarded:
                continue
            if AUTH_SIGNALS.search(body) or "getSessionUser" in body:
                continue
            if any(rel.startswith(d) for d in guard_dirs):
                continue
            client = bool(re.match(r"\s*['\"]use client['\"]", body))
            out.append(Finding(
                severity="blocker", code="UNGUARDED_PAGE", path=rel,
                message=(f"/{section} checks the signed-in role and this page "
                         f"under it does not, so anyone who knows the URL can "
                         f"open it"
                         + (" — and it is a Client Component, which cannot "
                            "read the session at all" if client else "")),
                fix=("Guard it the way the section's own page does: read the "
                     "session at the top of a SERVER component, redirect to "
                     "/login with no session and home for the wrong role. If "
                     "the page has to be interactive, keep the guard in the "
                     "server page and move the interactive part into a child "
                     "component.")))
        return out

    def exposed_secrets(self) -> list:
        out = []
        for rel, body in self._sources():
            for m in PUBLIC_SECRET_RE.finditer(body):
                out.append(Finding(
                    severity="blocker", code="EXPOSED_SECRET", path=rel,
                    message=(f"`{m.group(0)}` is a NEXT_PUBLIC_ variable, so "
                             f"its value is compiled into the browser bundle "
                             f"and readable by anyone"),
                    fix=("Drop the NEXT_PUBLIC_ prefix and read it only in "
                         "server code — a route handler or a server "
                         "component. If the browser genuinely needs the "
                         "result, add a route that uses the secret server-side "
                         "and returns just the answer.")))
                break
        return out

    def query_injection(self) -> list:
        out = []
        for rel, body in self._sources():
            if not rel.startswith("app/"):
                continue
            m = RAW_FILTER_RE.search(body)
            if m:
                out.append(Finding(
                    severity="major", code="QUERY_INJECTION", path=rel,
                    message=(f"a request body is passed straight to Mongo as "
                             f"the filter (`{m.group(0)[:40].strip()}…`), so a "
                             f"caller can send `{{\"$ne\": null}}` and match "
                             f"every document"),
                    fix=("Build the filter field by field from the values you "
                         "expect — `{ _id: new ObjectId(body.id) }` — never "
                         "hand the parsed body to the driver.")))
            elif WHERE_RE.search(body):
                out.append(Finding(
                    severity="major", code="QUERY_INJECTION", path=rel,
                    message="a `$where` clause is built by interpolation, "
                            "which runs the caller's string as JavaScript "
                            "inside the database",
                    fix="Replace `$where` with ordinary query operators."))
        return out

    def plain_passwords(self) -> list:
        out = []
        for rel, body in self._sources():
            if not PLAIN_PASSWORD_RE.search(body) or HASH_RE.search(body):
                continue
            out.append(Finding(
                severity="blocker", code="FAKE_HASH", path=rel,
                message="a password is compared or stored without hashing",
                fix=("Hash on the way in and verify with the same algorithm. "
                     "If the app uses Better Auth, let it own the password — "
                     "do not compare one by hand.")))
        return out

    def unsafe_html(self) -> list:
        out = []
        for rel, body in self._sources():
            m = UNSAFE_HTML_RE.search(body)
            if not m:
                continue
            out.append(Finding(
                severity="major", code="UNSAFE_HTML", path=rel,
                message=(f"`dangerouslySetInnerHTML` is fed "
                         f"`{m.group(1).strip()[:40]}`, which is not a literal "
                         f"— if it ever holds text a user supplied, that text "
                         f"runs as script"),
                fix=("Render it as text instead. React escapes `{value}` for "
                     "you, which is the whole reason this prop is named the "
                     "way it is.")))
        return out

    def audit(self) -> dict:
        """`npm audit`, as `{severity: count}`."""
        if not self.cmd:
            return {}
        res = self.cmd.run("npm audit --json", timeout=180)

        raw = (res.output or "").strip()
        start = raw.find("{")
        if start < 0:
            self._log("WARN", "   ⚠ npm audit produced no report")
            return {}
        try:
            data = json.loads(raw[start:])
        except json.JSONDecodeError as e:
            log.debug(f"npm audit json: {e}")
            return {}
        counts = ((data.get("metadata") or {}).get("vulnerabilities") or {})
        return {k: v for k, v in counts.items()
                if k in ("critical", "high", "moderate", "low") and v}

    def run(self, *, audit: bool = True) -> tuple:
        """Return `(findings, audit_counts)`."""
        findings = (self.unguarded_routes() + self.unguarded_pages()
                    + self.exposed_secrets() + self.plain_passwords()
                    + self.query_injection() + self.unsafe_html())
        counts = {}
        if audit:
            try:
                counts = self.audit()
            except Exception as e:
                self._log("WARN", f"   ⚠ npm audit failed: {e}")
                log.exception("npm audit")
        return findings, counts
