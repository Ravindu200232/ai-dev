"""Post-change semantic audit for feature/selection/pencil edits."""
from __future__ import annotations

import re

from .features_common import FeatureSpec, normalise_change_path, safe_change_path
from .workspace import WorkspaceTools, TOOL_HELP


AUDIT_SYSTEM = """\
You are the read-only reviewer of ONE completed change in a Next.js 16 app.
Do not write code. Do not congratulate the implementation. Your only job is to
prove whether the user's exact request is now satisfied by the CURRENT source.

Use the workspace tools when ownership or a dependency is uncertain. A build
being green is not proof of behavior. A filename is not proof of ownership.
For a visual selection/pencil edit, the selected route/file/element are strong
location evidence, but the actual behavior may live in a child component,
server action, API route, seed helper, shared caller or layout.

Output ONLY:
RESULT :: PASS|FAIL
GAP :: NONE | one precise remaining/wrong behavior
EVIDENCE :: <existing-path> :: concrete current source fact
EVIDENCE :: <existing-path> :: another fact when useful
FILE :: new|edit :: <path> :: server|client :: why this file must still change   (FAIL only)
VERIFY :: one concrete proof that would demonstrate completion
DONE

Rules:
- PASS only when the request is supported by current source evidence.
- FAIL only when you can name the missing/wrong behavior and source evidence.
- Never invent a FILE because its name sounds relevant. Inspect first.
- A large correction may name many files. There is no file-count limit.
- Do not turn unrelated cleanup, style preferences or test expectations into a gap.
"""


class FeaturesAgentAuditMixin:
    def _explicit_image_gap(self, request: str, spec: FeatureSpec, selected_path: str = "") -> dict | None:
        """Require a local generated-asset reference for explicit image features."""
        from .source_guidance import feature_image_requested
        if not feature_image_requested(request):
            return None
        live = getattr(self.arch, "files", {}) or {}
        touched = [p for p in dict.fromkeys((spec.written or []) +
                                             [f.get("path") for f in spec.files])
                   if p in live]
        candidates = []
        if selected_path in live:
            candidates.append(selected_path)
        route_hint = str(getattr(self, "route_hint", "") or "").split("?", 1)[0].rstrip("/") or "/"
        try:
            owner = str((self.az.enumerate_routes().get(route_hint) or {}).get("file") or "")
            if owner in live and owner not in candidates:
                candidates.append(owner)
        except Exception:
            pass
        candidates.extend(p for p in touched if p not in candidates and
                          (p.startswith("app/") or p.startswith("components/")))
        if any(re.search(r"/generated/[A-Za-z0-9._-]+\.(?:png|jpg|jpeg|webp)",
                         str(live.get(p) or ""), re.I) for p in candidates):
            return None
        owner = next((p for p in candidates if p.endswith(("page.jsx", "page.js", ".jsx"))),
                     candidates[0] if candidates else "")
        if not owner:
            return None
        return {
            "result": "FAIL",
            "gap": "the feature explicitly asks for an image, but the changed UI references no local /generated image asset",
            "evidence": [{"path": owner, "fact": "current changed UI contains no /generated/<name>.png image reference"}],
            "files": [{"path": owner, "action": "edit", "kind": "client",
                       "why": "add the requested image using a semantic /generated/<name>.png reference and useful alt text"}],
            "verify": "the changed UI references /generated/<semantic-name>.png and Fooocus produces that file",
        }

    def _audit_parse(self, text: str) -> dict:
        result = {"result": "", "gap": "", "evidence": [], "files": [],
                  "verify": ""}
        files = getattr(self.arch, "files", {}) or {}
        for raw in str(text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            head, sep, rest = line.partition("::")
            if not sep:
                continue
            kind, rest = head.strip().upper(), rest.strip()
            if kind == "RESULT":
                value = rest.split()[0].upper() if rest else ""
                if value in ("PASS", "FAIL"):
                    result["result"] = value
            elif kind == "GAP":
                result["gap"] = rest[:700]
            elif kind == "VERIFY":
                result["verify"] = rest[:700]
            elif kind == "EVIDENCE":
                parts = [p.strip().strip("`") for p in rest.split("::", 1)]
                if len(parts) == 2:
                    path = normalise_change_path(parts[0])
                    if path in files:
                        result["evidence"].append({"path": path,
                                                   "fact": parts[1][:700]})
            elif kind == "FILE":
                parts = [p.strip().strip("`") for p in rest.split("::")]
                if len(parts) >= 2:
                    action = parts[0].lower()
                    path = normalise_change_path(parts[1])
                    if action in ("new", "edit") and safe_change_path(path):
                        result["files"].append({
                            "path": path, "action": action,
                            "kind": parts[2].lower() if len(parts) > 2 else "",
                            "why": parts[3] if len(parts) > 3 else result["gap"],
                        })
        seen = set(); unique = []
        for f in result["files"]:
            if f["path"] not in seen:
                seen.add(f["path"]); unique.append(f)
        result["files"] = unique
        return result

    def _audit_source(self, paths) -> str:
        live = getattr(self.arch, "files", {}) or {}
        tools = WorkspaceTools(self.arch)
        out, used = [], 0
        budget = max(18000, int(self._budget_chars() * 0.28))
        for rel in dict.fromkeys(str(p or "") for p in paths or []):
            if rel not in live:
                continue
            block = (tools.read_file(rel) + "\n" + tools.importers(rel) +
                     "\n" + tools.dependency_closure(rel))
            if out and used + len(block) > budget:
                break
            out.append(block); used += len(block)
        return "\n\n".join(out)

    def audit_change(self, request: str, spec: FeatureSpec, *,
                     selected_path: str = "", selected_route: str = "",
                     selected_element: str = "") -> dict:
        """Return PASS or an evidence-backed delta plan; never mutate source."""
        image_gap = self._explicit_image_gap(request, spec, selected_path)
        if image_gap:
            return image_gap
        touched = list(dict.fromkeys((spec.written or []) +
                                     [f.get("path") for f in spec.files]))
        ctx = spec.context or {}
        receipt = "\n".join([
            f"CURRENT before change: {ctx.get('current') or '(not recorded)'}",
            f"GAP before change: {ctx.get('gap') or '(not recorded)'}",
            f"CAUSE/ownership: {ctx.get('cause') or '(not recorded)'}",
            f"Expected proof: {ctx.get('verify') or '(not recorded)'}",
        ])
        selected = ""
        if selected_path or selected_route or selected_element:
            selected = ("\n## Visual selection context\n"
                        f"Route: {selected_route or '/'}\n"
                        f"Selected owner candidate: {selected_path or '(unknown)'}\n"
                        f"Selected DOM/region: {selected_element or '(not described)'}\n")
        user = (f"## User request\n{request}\n\n"
                f"## Pre-change reasoning receipt\n{receipt}\n"
                f"{selected}\n"
                f"## Files written by the change\n" +
                "\n".join(f"- {p}" for p in touched if p) + "\n\n"
                f"## Current source/import evidence\n{self._audit_source(touched)}\n\n"
                "Inspect any additional owner/caller/route you need, then audit the exact request.")
        self.arch._workspace_tool_cache = {}
        convo = ([{"role": "system", "content": AUDIT_SYSTEM + "\n\n" + TOOL_HELP}]
                 + self._memory() + [{"role": "user", "content": user}])
        seen = set()
        for _ in range(3):
            buf = []
            try:
                self.arch._stream(convo, buf.append, temperature=0.1,
                                  model=self.model)
            except Exception as e:
                self._log("WARN", f"   ⚠ semantic audit failed: {str(e)[:100]}")
                return {"result": "UNKNOWN", "gap": str(e), "evidence": [],
                        "files": [], "verify": ""}
            reply = "".join(buf)
            convo.append({"role": "assistant", "content": reply})
            observations, used = WorkspaceTools(self.arch).serve(reply)
            if used:
                sig = observations.strip()
                if sig and sig not in seen:
                    seen.add(sig)
                    self._log("INFO", f"   🔎 semantic audit inspected {used} workspace tool(s)")
                    convo.append({"role": "user", "content":
                                  "Tool observations:\n\n" + observations +
                                  "\n\nContinue the SAME read-only audit. Output the complete RESULT protocol."})
                    continue
            result = self._audit_parse(reply)
            if result["result"] == "PASS" and result["evidence"]:
                return result
            if (result["result"] == "FAIL" and result["gap"] and
                    result["evidence"] and result["files"]):
                evidence_paths = {e.get("path") for e in result["evidence"]
                                  if isinstance(e, dict)}
                live = getattr(self.arch, "files", {}) or {}
                missing = [f.get("path") for f in result["files"]
                           if f.get("action") == "edit" and f.get("path") in live
                           and f.get("path") not in evidence_paths]
                if not missing:
                    return result
                convo.append({"role": "user", "content":
                              "The FAIL delta still names existing edit files without source evidence: "
                              + ", ".join(missing[:10]) +
                              ". Inspect those exact files/importers and output the COMPLETE audit protocol again. Do not guess."})
                continue
            convo.append({"role": "user", "content":
                          "That audit was not source-grounded. Do not guess. Inspect the relevant source and output RESULT, GAP, EVIDENCE, optional FILE lines, VERIFY, DONE."})
        return {"result": "UNKNOWN", "gap": "audit could not prove pass or a repair", "evidence": [],
                "files": [], "verify": ""}

    def converge_semantics(self, request: str, spec: FeatureSpec, *,
                           selected_path: str = "", selected_route: str = "",
                           selected_element: str = "", rounds: int = 2) -> FeatureSpec:
        """Close source-proven semantic gaps without looping on the same claim."""
        seen_gaps = set()
        for rnd in range(1, max(1, rounds) + 1):
            audit = self.audit_change(
                request, spec, selected_path=selected_path,
                selected_route=selected_route, selected_element=selected_element)
            if audit.get("result") == "PASS":
                self._log("INFO", f"   ✅ semantic proof — {str(audit.get('verify') or '')[:150]}")
                spec.context["audit"] = audit
                return spec
            if audit.get("result") != "FAIL":
                self._log("WARN", "   ⚠ semantic audit could not prove the change; build/runtime verification will remain authoritative")
                spec.context["audit"] = audit
                return spec
            gap = " ".join(str(audit.get("gap") or "").lower().split())
            if not gap or gap in seen_gaps:
                self._log("WARN", "   ↔ semantic audit repeated the same unresolved gap — stopping speculative rewrites")
                spec.context["audit"] = audit
                return spec
            seen_gaps.add(gap)
            self._log("WARN", f"   🔎 semantic gap round {rnd}: {audit.get('gap')[:180]}")
            delta = FeatureSpec(
                summary=f"Close semantic gap: {audit.get('gap')}",
                files=list(audit.get("files") or []),
                context={
                    "current": "post-change audit",
                    "gap": audit.get("gap") or "",
                    "cause": "source-backed semantic audit found incomplete behavior",
                    "evidence": list(audit.get("evidence") or []),
                    "verify": audit.get("verify") or "",
                    "confidence": "high",
                })
            if delta.is_empty():
                spec.context["audit"] = audit
                return spec
            n = self.apply(request + "\n\nClose this source-proven remaining gap only:\n" +
                           str(audit.get("gap") or ""), delta)
            if not n:
                spec.context["audit"] = audit
                return spec
            existing = {f.get("path") for f in spec.files}
            for f in delta.files:
                if f.get("path") not in existing:
                    spec.files.append(f); existing.add(f.get("path"))
            for p in delta.written:
                if p not in spec.written:
                    spec.written.append(p)
        spec.context["audit"] = self.audit_change(
            request, spec, selected_path=selected_path,
            selected_route=selected_route, selected_element=selected_element)
        return spec


__all__ = ["FeaturesAgentAuditMixin", "AUDIT_SYSTEM"]
