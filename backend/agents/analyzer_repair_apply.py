"""Repair application for analyzer findings."""
from .analyzer_common import *

class AnalyzerRepairApplyMixin:
    def _evidence_still_open(self, findings, proposed: dict, files: dict) -> list:
        """[(finding, detail)] for contract/data checks the candidate misses."""
        overlay = dict(files)
        overlay.update({k: v for k, v in (proposed or {}).items()
                        if isinstance(v, str)})

        def closure(rel, seen=None):
            from . import exports as _ex
            seen = seen if seen is not None else set()
            if not rel or rel in seen or rel not in overlay:
                return ""
            seen.add(rel)
            body = overlay.get(rel) or ""
            if not isinstance(body, str):
                return ""
            parts = [body]
            for stmt in _ex.parse_imports(body):
                target = _ex.resolve_local(rel, stmt.spec, overlay)
                if target:
                    parts.append(closure(target, seen))
            return "\n".join(parts)

        out = []
        for f in findings or []:
            code = getattr(f, "code", "")
            src = str(getattr(f, "path", "") or "")
            msg = str(getattr(f, "message", "") or "")
            if code == "BROKEN_CONTRACT":
                m = re.search(r"must reach (/[^\s,]+)", msg)
                if not m or not src:
                    continue
                target = m.group(1).rstrip("/")
                hay = closure(src)
                if target not in hay and target + "/" not in hay:
                    out.append((f, f"{src}: the exact string '{target}' is "
                                   f"still absent from the file and its "
                                   f"imports — write fetch('{target}') there"))
            elif code == "MISSING_PLANNED_DATA":
                m = re.search(r"collection '([^']+)'", msg)
                if not m or not src:
                    continue
                coll = m.group(1)
                api_url = f"/api/{coll.strip().lower()}"
                hay = closure(src)
                direct = re.search(
                    r"(?:getCollection|\.collection)\s*\(\s*['\"]"
                    + re.escape(coll) + r"['\"]", hay)
                if not direct and api_url not in hay:
                    out.append((f, f"{src}: still no data edge to '{coll}' — "
                                   f"either getCollection('{coll}') in the "
                                   f"file, or the literal fetch('{api_url}') "
                                   f"in it / a component it imports"))
            elif code == "MISSING_ACTION_ID":
                m = re.search(r'data-testid="([^"]+)"', str(getattr(f, "fix", "") or ""))
                if not m:
                    continue
                tid = m.group(1)
                if any(f'"{tid}"' in b or f"'{tid}'" in b
                       for b in overlay.values() if isinstance(b, str)):
                    continue
                out.append((f, f'the literal data-testid="{tid}" is still '
                               f"absent from every page and component"))
        return out

    def repair(self, report: AnalyzerReport, server_log: str = "") -> int:
        """Write the missing files."""
        allowed = set(report.missing)
        allowed |= {f.path for f in report.findings
                    if f.severity == "blocker" and f.path}

        allowed |= {p for f in report.findings
                    if f.severity == "blocker" for p in f.extra}

        allowed |= {f.path for f in report.findings
                    if f.code in REPAIRABLE_MAJOR and f.path}
        allowed |= {p for f in report.findings
                    if f.code in REPAIRABLE_MAJOR
                    for p in f.extra}
        if not allowed:
            return 0

        # What the FINDINGS name, before any expansion.
        direct = {str(p or "").strip().lstrip("./").replace("\\", "/")
                  for p in allowed}
        direct.discard("")

        files = self.source_files()
        routes = self.enumerate_routes()

        normalized = set()
        raw_allowed = {str(p or "").strip() for p in allowed if str(p or "").strip()}
        for item in raw_allowed:
            key = item.lstrip("./").replace("\\", "/")
            if key.startswith("/"):
                meta = routes.get(key) or routes.get(key.rstrip("/")) or {}
                rel = str(meta.get("file") or "").strip()
                if rel:
                    normalized.add(rel)
                continue
            normalized.add(key)
        allowed = normalized

        done = set(getattr(self, "_rewritten_this_stage", None) or ())
        skipped = set()

        contracts = (getattr(self.arch, "plan", None) or {}).get("contracts") or []
        for c in contracts:
            if not isinstance(c, dict):
                continue
            src = str(c.get("from") or "").lstrip("./").replace("\\", "/")
            target = str(c.get("target") or "").strip()
            tmeta = routes.get(target) or routes.get(target.rstrip("/")) or {}
            dst = str(tmeta.get("file") or "").strip()
            if ((src and src in allowed) or (dst and dst in allowed)
                    or target in raw_allowed or target.rstrip("/") in raw_allowed):
                for peer in (src, dst):
                    if not peer or peer not in files:
                        continue
                    if peer in done and peer not in direct:
                        skipped.add(peer)
                        continue
                    allowed.add(peer)

        # One-hop local UI components are part of the same repair
        local_import = re.compile(
            r"""from\s+['"]@/(components/[\w./-]+)['"]""")
        peers = set()
        for rel in list(allowed):
            body = files.get(rel, "")
            for spec in local_import.findall(body):
                for ext in (".jsx", ".js", ""):
                    child = spec + ext
                    if child in files:
                        (skipped if child in done and child not in direct
                         else peers).add(child)
                        break
        allowed |= peers
        if skipped:
            self._log("INFO", f"   \u21bb {len(skipped)} component(s) an earlier "
                              f"pass already rewrote are left alone: "
                              + ", ".join(sorted(skipped)[:4]))

        if not allowed:
            return 0

        helpers = sorted(p for p in files
                         if p.startswith("lib/") and p.endswith((".js", ".jsx")))

        helpers.sort(key=lambda p: ("seed" in p, p))
        context = []
        for helper in ["app/layout.js"] + helpers:
            body = files.get(helper)
            if body:
                budget = 1500 if "seed" in helper else 4000
                context.append(f"--- {helper} ---\n{body[:budget]}")

        rewrites = []
        for p in sorted(allowed):
            body = files.get(p)
            if body:
                rewrites.append(f"--- {p} (rewrite this, preserving everything "
                                f"that already works) ---\n{body[:6000]}")

        blockers = [f for f in report.findings
                    if f.severity == "blocker"
                    or f.code in REPAIRABLE_MAJOR]
        boundary_issue = any(
            any(marker in (getattr(f, "message", "") or "") for marker in (
                "Server Component", "Client Component",
                "Only plain objects can be passed", "Objects with toJSON",
                "Event handlers cannot be passed"))
            for f in blockers)
        listing = "\n".join(f"  • {p}" + ("  (exists — edit it)" if p in files
                                          else "  (new)")
                            for p in sorted(allowed))

        guidance = nextdocs.guidance_for(
            server_log + "\n" + "\n".join(f.message for f in blockers))

        capability_memory = (self.arch._capability_ledger(list(allowed))
                             if hasattr(self.arch, "_capability_ledger") else "")
        data_memory = (self.arch._data_ledger()
                       if hasattr(self.arch, "_data_ledger") else "")
        contract_memory = (self.arch._contract_ledger(list(allowed))
                           if hasattr(self.arch, "_contract_ledger") else "")
        truth = ""
        if capability_memory:
            truth += ("## Capability proof — make these behaviours actually observable\n"
                      + capability_memory + "\n\n")
        if data_memory:
            truth += ("## Canonical Data Model — keep these exact field names\n"
                      + data_memory + "\n\n")
        if contract_memory:
            truth += ("## Cross-file contracts touching this repair — both ends "
                      "must agree exactly\n" + contract_memory + "\n\n")

        user = (f"## The plan\n{self.plan_text()}\n\n"
                + truth
                + f"## Files that already exist\n{self.inventory()}\n\n"
                f"## Existing code you must integrate with\n"
                f"{chr(10).join(context)}\n\n"
                + (f"## Current contents of the files you are editing\n"
                   f"{chr(10).join(rewrites)}\n\n" if rewrites else "")
                + (f"## What the server printed while these requests failed\n"
                   f"```\n{server_log.strip()[-4000:]}\n```\n"
                   f"The frames above name the exact file and line. Fix what "
                   f"they point at, not what you assume the problem is.\n\n"
                   if server_log.strip() else "")
                + (guidance + "\n\n" if guidance else "")
                + f"## Problems to fix\n"
                f"{chr(10).join('  • ' + f.line() + (chr(10) + '    → ' + f.fix if f.fix else '') for f in blockers[:20])}\n\n"
                + ("SERVER/CLIENT BOUNDARY RULE: keep the page a Server "
                   "Component when it reads server data. Never pass onClick, "
                   "onChange, onRemove, onSubmit, or any function from that "
                   "server file into a Client Component. Move the handler and "
                   "the state it mutates into the client component itself, and "
                   "pass only serializable data from the server. Convert Mongo "
                   "ObjectId values to strings before crossing the boundary. "
                   "Do not add 'use client' to the server page just to silence "
                   "the error.\n\n" if boundary_issue else "")
                + f"Write these files, complete, and NOTHING else:\n{listing}\n\n"
                + ("For a file listed as (exists — edit it), keep everything it "
                   "already does and ADD what is missing. Deleting working "
                   "behaviour, or returning it unchanged, both count as "
                   "failure.\n\n" if rewrites else "")
                + f"Reuse the helpers above rather than reimplementing them — "
                f"import the session, database and permission functions that "
                f"already exist. Do not invent a second cookie name, a second "
                f"MongoClient or a parallel auth check.\n\n"
                f"Do NOT solve this repair by importing a NEW local component/helper "
                f"whose file is not in the existing inventory or the writable list. "
                f"That creates a module-not-found in the next build. Put the small "
                f"repair in an allowed file or reuse an existing local module.\n\n"
                f"One <write_file path=\"…\"> block per file.")

        convo = [{"role": "system", "content": self.arch._builder_sys()},
                 {"role": "user", "content": user}]

        written, rejected = [], []
        proposed = {}

        def on_end(path, content):
            key = (path or "").strip().lstrip("./").replace("\\", "/")
            if key:
                proposed[key] = content

        parser = FileStreamParser(
            on_text=lambda t: None,
            on_file_start=lambda p: None,
            on_file_token=lambda t: None,
            on_file_end=on_end)

        try:
            self.arch._stream(convo, parser.feed, temperature=0.4)
        except Exception as e:
            self._log("ERROR", f"   ❌ Analyzer repair failed: {e}")
        parser.close()

        # Prove the write closes its finding BEFORE it reaches disk.
        still = self._evidence_still_open(report.findings, proposed, files)
        if still:
            self._log("WARN", f"   🔁 {len(still)} machine check(s) still fail "
                              "in the candidate — one sharpened retry")
            retry = ["The rewrite you just produced still fails these exact "
                     "machine checks. They are grep-level string checks; "
                     "satisfy them literally."]
            retry_files = set()
            for finding, detail in still[:6]:
                retry.append(f"  • {detail}")
                retry_files.add(finding.path)
                retry_files.update(str(x) for x in (finding.extra or [])
                                   if str(x).endswith((".js", ".jsx")))
            retry.append("Rewrite ONLY these files, complete, one "
                         "<write_file> block each:\n"
                         + "\n".join(f"  • {p}" for p in sorted(retry_files)
                                     if p))
            convo.append({"role": "assistant",
                          "content": "\n".join(f"<write_file path=\"{p}\">…"
                                               for p in sorted(proposed))})
            convo.append({"role": "user", "content": "\n".join(retry)})
            reparser = FileStreamParser(
                on_text=lambda t: None,
                on_file_start=lambda p: None,
                on_file_token=lambda t: None,
                on_file_end=on_end)
            try:
                self.arch._stream(convo, reparser.feed, temperature=0.3)
            except Exception as e:
                self._log("WARN", f"   ⚠ sharpened retry failed: {e}")
            reparser.close()

        # The primary finding list is evidence, not a prison.
        safe_roots = ("app/", "components/", "lib/", "styles/")
        safe_root_files = {"middleware.js", "middleware.jsx", "middleware.ts",
                           "middleware.tsx"}

        def safe_path(key):
            if not key or key.startswith(("../", "/")) or "/../" in key:
                return False
            return key.startswith(safe_roots) or key in safe_root_files

        from pathlib import PurePosixPath
        import_rx = re.compile(
            r"(?:from\s+|import\s*\(|require\s*\()\s*['\"]([^'\"]+)['\"]")
        suffixes = (".jsx", ".js", ".tsx", ".ts", ".mjs", ".cjs")

        def local_candidates(importer, spec):
            if spec.startswith("@/"):
                raw = spec[2:]
            elif spec.startswith(("./", "../")):
                raw = (PurePosixPath(importer).parent / spec).as_posix()
            else:
                return []
            parts = []
            for bit in raw.split("/"):
                if bit == "..":
                    if parts:
                        parts.pop()
                elif bit not in ("", "."):
                    parts.append(bit)
            target = "/".join(parts)
            if not target:
                return []
            if any(target.endswith(ext) for ext in suffixes):
                return [target]
            return ([target] + [target + ext for ext in suffixes]
                    + [target + "/index" + ext for ext in suffixes])

        accepted = {p for p in proposed if p in allowed and safe_path(p)}
        expanded = set()
        changed = True
        while changed:
            changed = False
            for owner in list(accepted):
                body = proposed.get(owner, files.get(owner, "")) or ""
                for spec in import_rx.findall(body):
                    for candidate in local_candidates(owner, spec):
                        if candidate in proposed and candidate not in accepted \
                                and safe_path(candidate):
                            accepted.add(candidate)
                            expanded.add(candidate)
                            changed = True
                            break

        if expanded:
            self._log("INFO", f"   ↗ repair scope expanded by {len(expanded)} "
                              "direct dependency write(s): "
                              + ", ".join(sorted(expanded)[:6]))

        rejected = sorted(p for p in proposed if p not in accepted)
        for key in sorted(accepted):
            content = proposed[key]
            self._fire("on_file_start", key)
            self._fire("on_file_end", key, content)
            if self.arch.write_file(key, content):
                written.append(key)

        if rejected:
            self._log("WARN", f"   ⛔ Ignored {len(rejected)} unrelated/unsafe "
                              "repair write(s): " + ", ".join(rejected[:5]))
        self._files_cache = None
        report.written += len(written)
        if not hasattr(self, "_rewritten_this_stage"):
            self._rewritten_this_stage = set()
        self._rewritten_this_stage.update(written)
        return len(written)


