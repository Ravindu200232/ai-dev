"""Runtime route probing, inventory and model-assisted diagnosis."""
from .analyzer_common import *


class AnalyzerRuntimeMixin:
    @staticmethod
    def _post_json(url: str, payload: dict, timeout: int = 15):
        """(status, body). status is None when the server could not be reached."""
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(2000).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read(2000).decode("utf-8", "replace")
            except Exception:
                return e.code, ""
        except Exception:
            return None, ""

    def probe_pages(self, report: AnalyzerReport, *, skip_root: bool = False) -> None:
        """GET each non-dynamic page exactly once for the runtime stage."""
        for url, r in sorted(report.routes.items()):
            if r.get("kind") != "page" or r.get("dynamic"):
                continue
            if skip_root and url == "/":
                # Browser smoke already rendered / and captured JS/pageerror.
                continue
            status = self._get_status(self.base_url + url)
            if status is None:
                return
            bad = status >= 400
            self._fire("on_test", "fail" if bad else "pass",
                       f"Route {url}", f"HTTP {status}")
            if bad:
                report.findings.append(Finding(
                    "blocker", "ROUTE_ERROR",
                    f"{url} returns HTTP {status}",
                    path=r["file"],
                    fix=f"fix {r['file']} so {url} responds"))

    def probe_api_routes(self, report: AnalyzerReport, *, skip_health: bool = False) -> None:
        """GET each non-dynamic GET API exactly once in the API stage."""
        for url, r in sorted(report.routes.items()):
            if r.get("kind") != "api" or r.get("dynamic"):
                continue
            if "GET" not in (r.get("methods") or []):
                continue
            if skip_health and url == "/api/health":
                continue
            status = self._get_status(self.base_url + url)
            if status is None:
                return
            bad = status >= 500 or status == 404
            self._fire("on_test", "fail" if bad else "pass",
                       f"API {url}", f"HTTP {status}")
            if bad:
                report.findings.append(Finding(
                    "blocker", "ROUTE_ERROR",
                    f"{url} returns HTTP {status}",
                    path=r["file"],
                    fix=f"fix {r['file']} so {url} responds"))

    def probe_routes(self, report: AnalyzerReport) -> None:
        """Backward-compatible combined probe used by edit-time verification."""
        self.probe_pages(report)
        self.probe_api_routes(report)

    @staticmethod
    def _get_status(url: str, timeout: int = 60):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return None

    RUNTIME_HREF_RE = re.compile(r'''href=["'](/[^"']+)["']''', re.I)

    def probe_linked_dynamic_routes(self, report: AnalyzerReport, limit: int = 24) -> None:
        """Follow real record links rendered by static pages."""
        dynamic = {u: r for u, r in (report.routes or {}).items()
                   if r.get("kind") == "page" and r.get("dynamic")}
        if not dynamic:
            return
        examples = getattr(report, "runtime_examples", None)
        if not isinstance(examples, dict):
            examples = {}
            report.runtime_examples = examples
        seen = set()
        for origin, meta in sorted((report.routes or {}).items()):
            if len(seen) >= limit:
                break
            if meta.get("kind") != "page" or meta.get("dynamic"):
                continue
            try:
                with urllib.request.urlopen(self.base_url + origin, timeout=30) as resp:
                    if resp.status >= 400:
                        continue
                    html = resp.read(1_500_000).decode("utf-8", "replace")
            except Exception:
                continue
            for raw in self.RUNTIME_HREF_RE.findall(html):
                path = raw.split("#", 1)[0].split("?", 1)[0]
                if (not path.startswith("/")
                        or path.startswith(("/_next", "/api/", "/generated/"))):
                    continue
                route = next(((pat, r) for pat, r in dynamic.items()
                              if self._route_matches(path, [pat])), None)
                if not route or path in seen:
                    continue
                seen.add(path)
                pat, r = route
                rel = str(r.get("file") or "")
                if rel:
                    bucket = examples.setdefault(rel, [])
                    if raw not in bucket:
                        bucket.append(raw)
                status = self._get_status(self.base_url + raw, timeout=45)
                if status is None:
                    continue
                bad = status == 404 or status >= 500
                self._fire("on_test", "fail" if bad else "pass",
                           f"Linked route {raw}", f"HTTP {status}")
                if bad:
                    report.findings.append(Finding(
                        "blocker", "DYNAMIC_ROUTE_ERROR",
                        f"{origin} renders a real link to {raw}, which matches {pat} "
                        f"but returns HTTP {status}; the detail route exists on disk "
                        "yet a real record link is broken",
                        path=r.get("file", ""),
                        fix=(f"repair {r.get('file','the detail page')} for the real "
                             f"runtime id carried by {raw}; keep the list/detail data "
                             "types consistent instead of removing the link"),
                        extra=[meta.get("file", ""), r.get("file", "")]))
                if len(seen) >= limit:
                    break

    def inventory(self) -> str:
        """One line per file: enough to reason about the project without reading it."""
        lines = []
        for path, content in sorted(self.code_files().items()):
            first = next((l.strip() for l in content.splitlines() if l.strip()), "")
            directive = "'use client'" if first.startswith(("'use client'", '"use client"')) else "server"
            exports = re.findall(
                r"export\s+(?:default\s+)?(?:async\s+)?(?:function|const)\s+(\w+)",
                content)
            if re.search(r"export\s+default\s+(?:async\s+)?function\s*\(", content):
                exports.append("default")
            pkgs = self.arch.imported_packages(content)
            lines.append(
                f"{path} · {len(content) // 1024 or 1}KB · {directive}"
                + (f" · exports {', '.join(exports[:4])}" if exports else "")
                + (f" · uses {', '.join(pkgs[:4])}" if pkgs else ""))
        for path in sorted(self.source_files()):
            if path.endswith(".css") or path == "package.json":
                lines.append(f"{path} · (not code)")
        return "\n".join(lines)

    def route_table(self, routes: dict) -> str:
        rows = []
        for url in sorted(routes):
            r = routes[url]
            m = "/".join(r["methods"]) if r["methods"] else "-"
            rows.append(f"{url}  →  {r['file']}  [{m}]")
        return "\n".join(rows)

    READ_RE = re.compile(r"<read_file\s+path\s*=\s*[\"']([^\"'>]+)[\"']\s*/?>", re.I)

    def _read_for_model(self, rel: str) -> str:
        """Serve one `read_file`. Refuses anything outside the project or secret."""
        try:
            fp = self.arch._safe_path(rel)
            key = str(fp.relative_to(self.project_dir)).replace("\\", "/")
        except Exception:
            return f"[refused: {rel} is not a path in this project]"
        if Path(key).name.startswith(".env"):
            return "[refused: environment files are not readable]"
        body = self.source_files().get(key)
        if body is None:
            if not fp.exists():
                return f"[{rel} does not exist]"
            return f"[refused: {rel} is not a source file]"
        return body

    def _budget_chars(self) -> int:
        """Scale the read loop to whatever model the build is using."""
        return int(getattr(self.arch, "num_ctx", 16384) * 0.55 * 3.4)

    def diagnose(self, report: AnalyzerReport, max_reads: int = 12) -> list:
        """Ask the model what the fixed pass cannot see: whether the app actually does."""
        system = (
            "You are auditing a finished Next.js 16 App Router + MongoDB "
            "project against the plan it was built from.\n\n"
            "You are given the plan, an inventory of every source file, the "
            "routes the app serves, and the problems a static checker already "
            "found. To read a file, emit exactly:\n"
            "<read_file path=\"lib/seed.js\"/>\n"
            "One per line; you will be given the contents and may then read "
            "more.\n\n"
            "Look for things a checker cannot: features the plan promises that "
            "no code implements, a collection the Data Model describes that "
            "nothing ever reads, a page that renders nothing real, seed data "
            "that does not match the plan.\n\n"
            "When finished, output one line per problem, nothing else:\n"
            "FINDING <blocker|major|minor> <path-or-> :: <what is wrong>\n"
            "If the project matches its plan, output exactly: FINDING none - :: ok")

        user = (f"## The plan\n{self.plan_text()}\n\n"
                f"## Files\n{self.inventory()}\n\n"
                f"## Routes served\n{self.route_table(report.routes)}\n\n"
                f"## Already found by the static checker\n"
                f"{report.as_prompt_block() or '(nothing)'}\n\n"
                f"Read what you need, then report.")

        convo = [{"role": "system", "content": system},
                 {"role": "user", "content": user}]
        budget = self._budget_chars()
        findings, reads = [], 0

        while True:
            buf = []
            try:
                self.arch._stream(convo, buf.append, temperature=0.3)
            except Exception as e:
                self._log("WARN", f"   ⚠ Analyzer query failed: {e}")
                break
            reply = "".join(buf)
            convo.append({"role": "assistant", "content": reply})

            wanted = self.READ_RE.findall(reply)
            used = sum(len(m["content"]) for m in convo)
            if wanted and reads < max_reads and used < budget:
                served = []
                for rel in wanted[:4]:
                    reads += 1
                    served.append(f"--- {rel} ---\n{self._read_for_model(rel)}")
                self._log("INFO", f"   📖 read {', '.join(wanted[:4])}")
                convo.append({"role": "user", "content": "\n\n".join(served)})
                continue

            for line in reply.splitlines():
                m = re.match(r"\s*FINDING\s+(\w+)\s+(\S+)\s*::\s*(.+)", line)
                if not m:
                    continue
                sev, path, msg = m.group(1).lower(), m.group(2), m.group(3).strip()
                if sev == "none":
                    break
                if sev not in SEVERITIES:
                    sev = "minor"
                findings.append(Finding(sev, "REVIEW", msg,
                                        path=("" if path == "-" else path)))
            break

        if findings:
            self._log("INFO", f"   🔍 Model reported {len(findings)} extra "
                              f"problem(s)")
        return findings

    SELF_DESTRUCTURE_RE = re.compile(
        r"const\s*\{\s*(params|searchParams)\s*:\s*\w+[^}]*\}\s*=\s*await\s+\1\b")

    def async_param_confusion(self) -> list:
        """`const { searchParams: x } = await searchParams` — a guaranteed 500."""
        out = []
        for path, content in sorted(self.code_files().items()):
            for m in self.SELF_DESTRUCTURE_RE.finditer(content):
                name = m.group(1)
                line = content[:m.start()].count("\n") + 1
                out.append(Finding(
                    "blocker", "ASYNC_PARAM_CONFUSION",
                    f"line {line} does `const {{ {name}: … }} = await {name}` — "
                    f"awaiting `{name}` already yields the object, so this pulls "
                    f"a `{name}` key that does not exist and the result is "
                    f"undefined. The first property read on it throws, which is "
                    f"a 500 on a page that compiles",
                    path=path,
                    fix=f"`const {{ … }} = await {name}` — destructure the keys "
                        f"you actually want straight out of it"))
        return out

    def orphan_components(self) -> list:
        """Components under `components/` that no file imports."""
        files = self.code_files()
        names = {Path(p).stem for p in files if p.startswith("components/")}
        blob = "\n".join(files.values())
        return sorted(
            n for n in names
            if not re.search(r"""from\s+['"][^'"]*/""" + re.escape(n) + r"""['"]""",
                             blob))
