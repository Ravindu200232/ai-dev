"""Focused planning responsibilities for FeaturesAgent."""


# The change format's own line kinds.
_SPEC_KEYWORDS = {"FILE", "PACKAGE", "ROUTE", "EVIDENCE", "VERIFY", "SUMMARY",
                  "GAP", "CAUSE", "CONFIDENCE", "NEW", "EDIT", "NONE"}
from .features_common import *


class FeaturesAgentPlanningMixin:
    def plan_feature(self, request: str, max_reads: int | None = MAX_READS) -> FeatureSpec:
        routes = self.az.enumerate_routes()
        route_hint = str(getattr(self, "route_hint", "") or "").split("?", 1)[0].rstrip("/") or "/"
        focus = []
        meta = routes.get(route_hint) or {}
        route_owner = str(meta.get("file") or "")
        if route_owner:
            focus.append(route_owner)
        for rel in self.feature_focus_paths(request):
            if rel not in focus:
                focus.append(rel)
        source = self._focused_source(focus, budget=int(self._budget_chars() * 0.20))
        current_ui = (f"## Current preview route\n{route_hint} is the page the user was looking at. "
                      f"Its source owner is {route_owner or '(not resolved)'}. Treat this as strong ownership evidence; "
                      "do not invent an alternate page for the same workflow unless the request explicitly asks for a new route.\n\n")
        from .source_guidance import feature_image_prompt
        image_contract = feature_image_prompt(request)
        user = (f"## The project's plan\n{self.az.plan_text()[:6000]}\n\n"
                f"## Routes it serves\n{self.az.route_table(routes)}\n\n"
                + current_ui +
                f"## The feature to add\n{request}\n\n"
                + (("## Image generation contract\n" + image_contract + "\n") if image_contract else "") +
                f"## Complete source inventory\n{self.az.inventory()}\n\n"
                f"## Request-relevant source already retrieved\n{source or '(none)'}\n\n"
                f"Use workspace tools for any importer/helper/route you still need. "
                f"Work out what this feature touches — including files that only "
                f"break BECAUSE of it — then output the plan lines.")
        return self._plan(PLAN_SYSTEM, user, max_reads, "Feature planning", mode="feature")

    def plan_change(self, request: str, *, selected_path: str = "",
                    selected_route: str = "", selected_element: str = "") -> FeatureSpec:
        """Plan any requested app change before a visual editor writes."""
        routes = self.az.enumerate_routes()
        focus = []
        if selected_path:
            focus.append(selected_path)
        for rel in self.feature_focus_paths(request):
            if rel not in focus:
                focus.append(rel)
        source = self._focused_source(focus, budget=int(self._budget_chars() * 0.24))
        selected = ""
        if selected_path or selected_route or selected_element:
            selected = ("## The exact UI context\n"
                        f"Route: {selected_route or '/'}\n"
                        f"Current source file: {selected_path or '(unknown)'}\n"
                        f"Selected region: {selected_element or '(not described)'}\n\n")
        user = (f"## The project's plan\n{self.az.plan_text()[:6000]}\n\n"
                f"## Routes it serves\n{self.az.route_table(routes)}\n\n"
                + selected +
                f"## The requested change\n{request}\n\n"
                f"## Complete source inventory\n{self.az.inventory()}\n\n"
                f"## Request-relevant source already retrieved\n{source or '(none)'}\n\n"
                "Analyze impact first. Name every file that must change. A visual "
                "edit is allowed to be one file or many files; do not force it into "
                "one file when dependencies or runtime boundaries require more.")
        return self._plan(PLAN_SYSTEM, user, None, "Change impact analysis",
                          mode="visual", required_evidence_paths=[selected_path] if selected_path else None)

    def cover_whole_request(self, request: str, spec: FeatureSpec) -> FeatureSpec:
        """The plan, checked against the whole request before anything is written."""
        if not spec.files:
            return spec
        listing = "\n".join(f"  {f.get('action', 'edit'):4} {f['path']}"
                            for f in spec.files)
        ask = (f"## What was asked for\n{request}\n\n"
               f"## What the plan says it will do\n{spec.summary or '(no summary)'}\n\n"
               f"## The files it will change\n{listing}\n\n"
               f"Name the parts of the request no file above will deliver.")
        buf = []
        try:
            self.arch._stream([{"role": "system", "content": self.COVER_SYSTEM},
                               {"role": "user", "content": ask}],
                              buf.append, temperature=0.1, model=self.model)
        except Exception as e:
            self._log("WARN", f"   ⚠ could not check the plan covers the "
                              f"request ({str(e)[:60]}) — building it as planned")
            return spec

        gaps = [(m.group(1).strip(), m.group(2).strip()) for m in
                re.finditer(r"^\s*MISSING\s*::\s*(.+?)\s*::\s*(.+?)\s*$",
                            "".join(buf), re.M)]
        if not gaps:
            return spec

        self._log("WARN", f"   🧩 the plan leaves {len(gaps)} part(s) of the "
                          f"request undone — asking for them too")
        for what, where in gaps:
            self._log("WARN", f"      · {what[:90]} → {where}")

        tail = "\n".join(f"- {what}  (belongs in {where})" for what, where in gaps)
        again = self.plan_feature(
            f"{request}\n\nA first plan missed these parts of it. The new plan "
            f"must deliver them as well as everything else:\n{tail}")
        if again.is_empty():
            self._log("WARN", "   ⚠ the second plan came back empty — keeping "
                              "the first")
            return spec

        # Union, not replacement.
        have = spec.paths()
        merged = list(spec.files) + [f for f in again.files
                                     if f["path"] not in have]
        added = len(merged) - len(spec.files)
        spec.files = merged
        spec.summary = again.summary or spec.summary
        for pkg in again.packages:
            if pkg not in spec.packages:
                spec.packages.append(pkg)

        # Keep the source proof that justified the added coverage files.
        ctx = spec.context or {}
        more = again.context or {}
        ev = list(ctx.get("evidence") or [])
        seen_ev = {str(x.get("path") or "") for x in ev if isinstance(x, dict)}
        for item in (more.get("evidence") or []):
            if isinstance(item, dict) and item.get("path") not in seen_ev:
                ev.append(item); seen_ev.add(item.get("path"))
        ctx["evidence"] = ev
        if more.get("cause"):
            base = str(ctx.get("cause") or "").strip()
            extra = str(more.get("cause") or "").strip()
            ctx["cause"] = (base + "; coverage analysis: " + extra) if base else extra
        if more.get("verify"):
            ctx["verify"] = str(more.get("verify"))
        if more.get("confidence") in ("high", "medium"):
            ctx["confidence"] = more.get("confidence")
        spec.context = ctx

        self._log("INFO", f"   ✅ {added} more file(s) planned so the whole "
                          f"request gets built")
        return spec

    def repair_focus_paths(self, focus_paths=None, evidence: str = "") -> list[str]:
        """Small evidence-driven source neighborhood for an E2E/runtime bug."""
        files = getattr(self.arch, "files", {}) or {}
        chosen, queue = [], []

        def add(rel, *, allow_missing=False):
            rel = str(rel or "").strip().lstrip("./").replace("\\", "/")
            valid = rel.startswith(("app/", "components/", "lib/")) and \
                    rel.endswith((".js", ".jsx"))
            # An explicitly failing/missing route may not exist yet.
            if valid and (allow_missing or rel in files) and rel not in chosen:
                chosen.append(rel)
                queue.append(rel)

        for rel in focus_paths or []:
            add(rel, allow_missing=True)
        for rel in re.findall(r"\b(?:app|components|lib)/[A-Za-z0-9_./\[\]-]+\.(?:js|jsx)\b",
                              evidence or ""):
            add(rel, allow_missing=True)

        routes = self.az.enumerate_routes()
        for url in re.findall(r"(?:https?://[^/\s]+)?(/api/[A-Za-z0-9_./\[\]-]+)",
                              evidence or ""):
            clean = url.split("?")[0].rstrip("/") or "/"
            for route, meta in routes.items():
                if self.az._route_matches(clean, [route]):
                    add(meta.get("file"))
                    break

        authish = bool(re.search(
            r"\b401\b|unauthori[sz]ed|sign.?in|log.?in|session|/api/auth/",
            evidence or "", re.I))
        if authish:
            login_candidates = [p for p in ("app/login/page.jsx",
                                             "app/login/page.js") if p in files]
            if login_candidates:
                add(login_candidates[0])
            else:
                add("app/login/page.jsx", allow_missing=True)
            for rel in ("lib/auth-client.js", "lib/auth.js",
                        "app/api/auth/[...all]/route.js"):
                add(rel)

        # One-hop local imports from the actual failing files.
        for rel in list(queue)[:8]:
            body = str(files.get(rel, "") or "")
            for target in re.findall(
                    r"(?:from\s+|import\s*\()\s*['\"](@/|\.{1,2}/)([^'\"]+)['\"]",
                    body):
                prefix, tail = target
                if prefix == "@/":
                    base = tail
                else:
                    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                    parts = (parent + "/" + prefix + tail).split("/")
                    norm = []
                    for part in parts:
                        if part in ("", "."):
                            continue
                        if part == "..":
                            if norm: norm.pop()
                        else:
                            norm.append(part)
                    base = "/".join(norm)
                for cand in (base, base + ".js", base + ".jsx",
                             base + "/index.js", base + "/index.jsx"):
                    add(cand)
        return chosen

    def plan_repair(self, errors: str, *, server_log: str = "",
                    max_reads: int | None = MAX_READS, focus_paths=None) -> FeatureSpec:
        """Same two-phase shape, pointed at a bug instead of a feature."""
        routes = self.az.enumerate_routes()

        # The source, not an inventory.
        focus = self.repair_focus_paths(
            focus_paths, "\n".join([errors or "", server_log or ""])) if focus_paths else []
        if focus:
            source = self._focused_source(
                focus, budget=int(self._budget_chars() * 0.24))
        else:
            source = self.full_source(budget=int(self._budget_chars() * 0.42))
        parts = [f"## The project's plan\n{self.az.plan_text()[:4000]}",
                 (("## Focused source neighborhood — plan changes inside this "
                   "set unless the error explicitly names another file\n" + source)
                  if focus else (f"## The source\n{source}" if source
                  else f"## Every source file\n{self.az.inventory()}")),
                 f"## Routes it serves\n{self.az.route_table(routes)}",
                 f"## What went wrong when the app ran\n```\n{errors[:6000]}\n```"]
        if server_log.strip():
            parts.append("## The dev server's own output\n"
                         f"```\n{server_log[:4000]}\n```")
        parts.append("Read what you need, then output the plan lines.")
        return self._plan(REPAIR_SYSTEM, "\n\n".join(parts), max_reads,
                          "Repair planning", allow_empty=True, mode="repair",
                          investigation_paths=focus or None)

    def _analysis_issues(self, spec: FeatureSpec, mode: str,
                             required_evidence_paths=None) -> list[str]:
        """Return evidence gaps that make a proposed change speculative."""
        ctx = spec.context or {}
        issues = []
        for key in ("current", "gap", "cause", "verify"):
            if not str(ctx.get(key) or "").strip():
                issues.append(f"missing {key.upper()} analysis")
        evidence = ctx.get("evidence") or []
        evidence_paths = {str(e.get("path") or "").replace("\\", "/")
                          for e in evidence if isinstance(e, dict)}

        files = getattr(self.arch, "files", {}) or {}

        # A page that does not exist has no source to quote. Asking for
        # evidence inside it is unanswerable, and a 404 whose whole fix is
        # "write the file" spent five rounds being told to prove it.
        creating = [f.get("path") for f in (spec.files or [])
                    if f.get("action") == "create"
                    or str(f.get("path") or "") not in files]
        creating = [p for p in creating if p]

        if not evidence_paths and not creating:
            issues.append("no source EVIDENCE was cited")
        existing_edits = [f["path"] for f in spec.files
                          if f.get("action") == "edit" and f.get("path") in files]
        missing = [p for p in existing_edits if p not in evidence_paths]
        if missing:
            issues.append("planned edit(s) not yet evidenced: " + ", ".join(missing[:12]))

        for rel in required_evidence_paths or []:
            rel = str(rel or "").replace("\\", "/")
            if rel and rel in files and rel not in evidence_paths:
                issues.append(f"selected/failing owner {rel} was not evidenced")

        confidence = str(ctx.get("confidence") or "").strip().lower()
        if confidence not in ("high", "medium", "low"):
            issues.append("missing CONFIDENCE")
        elif confidence == "low":
            issues.append("analysis confidence is low")
        return issues

    def _evidence_bundle(self, paths) -> str:
        """Deterministically expose source/import graph for unproven edits."""
        tools = WorkspaceTools(self.arch)
        files = getattr(self.arch, "files", {}) or {}
        out, used = [], 0
        budget = max(12000, int(self._budget_chars() * 0.18))
        for rel in paths or []:
            rel = str(rel or "").strip().replace("\\", "/")
            if not rel or rel not in files:
                continue
            blocks = [tools.read_file(rel), tools.importers(rel),
                      tools.dependency_closure(rel)]
            block = f"### Evidence for {rel}\n" + "\n".join(blocks)
            if out and used + len(block) > budget:
                break
            out.append(block)
            used += len(block)
        return "\n\n".join(out)

    def _plan(self, system: str, user: str, max_reads: int | None, what: str,
              *, allow_empty: bool = False, mode: str = "feature",
              required_evidence_paths=None, investigation_paths=None) -> FeatureSpec:
        """Read → hypothesize → prove → impact-map until evidence converges."""
        self.arch._workspace_tool_cache = {}
        convo = ([{"role": "system", "content": system + "\n\n" + TOOL_HELP}]
                 + self._memory()
                 + [{"role": "user", "content": user}])
        budget, reads = self._budget_chars(), 0
        spec = FeatureSpec()
        seen_tool_observations = set()
        previous_state = None
        best_evidence = 0
        no_progress = 0
        analysis_round = 0
        emergency_turn_cap = 30  # Loop-safety only; never a file-count ceiling

        while analysis_round < emergency_turn_cap:
            analysis_round += 1
            tool_progress = False
            while True:
                buf = []
                try:
                    self.arch._stream(convo, buf.append, temperature=0.22,
                                      model=self.model)
                except Exception as e:
                    self._log("WARN", f"   ⚠ {what} failed: {e}")
                    return spec
                reply = "".join(buf)
                convo.append({"role": "assistant", "content": reply})

                used_chars = sum(len(str(m.get("content", ""))) for m in convo)
                observations, tool_count = WorkspaceTools(self.arch).serve(reply)
                if tool_count and used_chars < budget * 0.92:
                    sig = observations.strip()
                    if sig and sig in seen_tool_observations:
                        self._log("WARN", f"   ↔ {what}: repeated the same workspace read — deciding with current evidence")
                    else:
                        if sig:
                            seen_tool_observations.add(sig)
                        reads += tool_count
                        tool_progress = True
                        self._log("INFO", f"   🧰 {what}: inspected {tool_count} workspace tool(s) ({reads} total)")
                        convo.append({"role": "user", "content":
                                      "Tool observations:\n\n" + observations +
                                      "\n\nContinue the SAME analysis. Do not write code. Establish CURRENT, GAP, CAUSE and source EVIDENCE before naming the impact files."})
                        continue
                break

            spec = self._parse(reply)
            issues = self._analysis_issues(spec, mode, required_evidence_paths)
            if not spec.is_empty() and not issues:
                self._log("INFO", f"   🧠 root cause/gap — {str(spec.context.get('cause') or '')[:160]}")
                self._log("INFO", f"   🔎 evidence — {len(spec.context.get('evidence') or [])} source fact(s), confidence {spec.context.get('confidence')}")
                return spec

            if allow_empty and spec.is_empty():
                ctx = spec.context or {}
                if (ctx.get("current") and ctx.get("gap") and ctx.get("cause") and
                        ctx.get("evidence") and ctx.get("verify") and
                        ctx.get("confidence") in ("high", "medium")):
                    self._log("INFO", f"   ✅ {what}: evidence points to no source change")
                    return spec

            ctx = spec.context or {}
            ev = {str(e.get("path") or "") for e in (ctx.get("evidence") or [])
                  if isinstance(e, dict) and e.get("path")}
            evidence_count = len(ev)
            state = (
                tuple(sorted(f.get("path", "") for f in spec.files)),
                tuple(sorted(ev)),
                tuple(issues),
                str(ctx.get("cause") or "")[:180],
            )
            progressed = tool_progress or evidence_count > best_evidence
            if progressed:
                best_evidence = max(best_evidence, evidence_count)
                no_progress = 0
            elif previous_state == state:
                no_progress += 1
            else:
                # A changed hypothesis is worth one chance.
                no_progress = max(0, no_progress - 1)
            previous_state = state

            used_chars = sum(len(str(m.get("content", ""))) for m in convo)
            if no_progress >= 2:
                self._log("WARN", f"   ↔ {what}: source analysis stopped making progress")
                break
            if used_chars >= budget * 0.92:
                self._log("WARN", f"   ⚠ {what}: context is full before the impact map was proven")
                break

            files = getattr(self.arch, "files", {}) or {}
            missing = [f.get("path") for f in spec.files
                       if f.get("action") == "edit" and f.get("path") in files
                       and f.get("path") not in ev]
            for rel in required_evidence_paths or []:
                if rel and rel in files and rel not in ev and rel not in missing:
                    missing.append(rel)

            protocol_gap = any(x.startswith("missing ") or
                               x == "no source EVIDENCE was cited"
                               for x in issues)
            if protocol_gap or not missing:
                for rel in investigation_paths or []:
                    rel = str(rel or "").replace("\\", "/")
                    if rel in files and rel not in missing:
                        missing.append(rel)
                for rel in sorted(ev):
                    if rel in files and rel not in missing:
                        missing.append(rel)

            bundle = self._evidence_bundle(missing)
            issue_text = "; ".join(issues[:5] or ["analysis protocol incomplete"])
            self._log("WARN", f"   🔎 analysis needs proof — {issue_text[:420]}")
            convo.append({"role": "user", "content":
                "The proposed impact map is still speculative:\n- " +
                "\n- ".join(issues or ["analysis protocol incomplete"]) +
                ("\n\nController-provided source/import evidence for the unresolved points:\n" + bundle if bundle else "") +
                "\n\nImportant: symptom/focus files are investigation anchors, not mandatory edit owners. "
                "Follow the dependency/data-flow evidence to the real owner. Every EXISTING file you plan to edit must have its own concrete EVIDENCE line. "
                "Re-evaluate the root cause/ownership and output the COMPLETE analysis protocol again: CURRENT, GAP, CAUSE, EVIDENCE lines, SUMMARY, FILE lines, VERIFY, CONFIDENCE, DONE. Do not write code."})

        # The rounds above are for making the analysis better, not for
        # forbidding the attempt. What is left is a model that named a root
        # cause and named the files, but could not quote a source line in the
        # exact shape the protocol wanted — which is a formatting miss, not a
        # reason to leave a broken app broken.
        #
        # Letting it try is safe here because nothing downstream trusts the
        # attempt: the patch is parsed, the project is built, and a snapshot
        # is restored the moment either fails. Gate the RESULT, not the try.
        return self._unproven_but_actionable(
            spec, self._analysis_issues(spec, mode, required_evidence_paths),
            what)

    # The smallest thing worth calling a cause. Shorter than this is a label.
    MIN_CAUSE_CHARS = 12

    def _unproven_but_actionable(self, spec, issues, what: str):
        """A repair that named a cause and a file may run, proof or not.

        The rounds above exist to make the analysis better, not to forbid the
        attempt. What reaches here is a model that worked out a root cause and
        the files it lives in, but could not quote a source line in the exact
        shape the protocol wanted — a formatting miss, not a reason to leave a
        broken app broken.
        """
        files = getattr(self.arch, "files", {}) or {}
        planned = [f for f in (spec.files or [])
                   if str(f.get("path") or "") in files
                   or f.get("action") == "create"]
        cause = str((getattr(spec, "context", {}) or {}).get("cause")
                    or getattr(spec, "summary", "") or "").strip()

        if planned and len(cause) >= self.MIN_CAUSE_CHARS:
            self._log("WARN",
                      f"   🤝 {what}: the analysis protocol is incomplete "
                      f"({'; '.join(issues[:3]) or 'unproven'}), but a root "
                      f"cause and {len(planned)} file(s) were named — letting "
                      f"the repair run. It is reverted if it does not parse "
                      f"or does not build.")
            spec.files = planned
            return spec

        self._log("WARN", f"   ⚠ {what} named neither a cause nor a file it "
                          f"could change: " +
                  "; ".join(issues[:6] or ["analysis inconclusive"]))
        return FeatureSpec(context=spec.context)


    def _parse(self, reply: str) -> FeatureSpec:
        spec = FeatureSpec(context={
            "current": "", "gap": "", "cause": "", "evidence": [],
            "verify": "", "confidence": "",
        })
        for kind, rest in PLAN_LINE_RE.findall(reply):
            rest = rest.strip().strip("`")
            if kind == "CURRENT":
                spec.context["current"] = rest[:500]
            elif kind == "GAP":
                spec.context["gap"] = rest[:500]
            elif kind == "CAUSE":
                spec.context["cause"] = rest[:700]
            elif kind == "EVIDENCE":
                parts = [p.strip().strip("`") for p in rest.split("::", 1)]
                if len(parts) == 2:
                    path = normalise_change_path(parts[0])
                    files = getattr(self.arch, "files", {}) or {}
                    if path in files:
                        spec.context["evidence"].append({"path": path, "fact": parts[1][:700]})
            elif kind == "VERIFY":
                spec.context["verify"] = rest[:700]
            elif kind == "CONFIDENCE":
                spec.context["confidence"] = rest.split()[0].lower() if rest else ""
            elif kind == "SUMMARY":
                spec.summary = rest[:200]
            elif kind == "PACKAGE" and rest:
                name = rest.split("::")[0].strip().strip("`")
                # An npm name is lowercase -- npm has refused uppercase
                if (self.arch.PKG_NAME_RE.match(name)
                        and name == name.lower()
                        and name.upper() not in _SPEC_KEYWORDS
                        and name not in self.arch.NODE_BUILTINS):
                    spec.packages.append(name)
            elif kind == "ROUTE" and rest.startswith("/"):
                spec.routes.append(rest.split()[0])
            elif kind == "FILE":
                parts = [p.strip().strip("`") for p in rest.split("::")]
                if len(parts) < 2:
                    continue
                action = parts[0].lower()
                path = normalise_change_path(parts[1])
                if action not in ("new", "edit") or not safe_change_path(path):
                    continue
                spec.files.append({
                    "path": path, "action": action,
                    "kind": (parts[2].lower() if len(parts) > 2 else ""),
                    "why": (parts[3] if len(parts) > 3 else "")})

        seen, unique = set(), []
        for f in spec.files:
            if f["path"] in seen:
                continue
            seen.add(f["path"])
            unique.append(f)
        spec.files = unique
        spec.packages = list(dict.fromkeys(spec.packages))
        # Stable de-duplication of evidence paths/facts.
        ev_seen, ev = set(), []
        for item in spec.context.get("evidence") or []:
            sig = (item.get("path"), item.get("fact"))
            if sig in ev_seen:
                continue
            ev_seen.add(sig); ev.append(item)
        spec.context["evidence"] = ev
        return spec

