"""Plan JSON extraction, normalization and fallback plan behavior."""
from .architect_common import *


class ArchitectPlanNormalizeMixin:
    def _extract_plan_json(self, raw: str) -> dict:
        blocks = re.findall(r"```json\s*(.*?)```", raw, re.S)
        blocks += re.findall(r"```\s*(\{.*?\})\s*```", raw, re.S)
        for b in reversed(blocks):
            try:
                data = json.loads(b.strip())
                if isinstance(data, dict) and (data.get("tasks")
                                               or data.get("phases")):
                    return self._normalise_plan(data)
            except json.JSONDecodeError:
                continue

        i, j = raw.find("{"), raw.rfind("}")
        if i >= 0 and j > i:
            try:
                data = json.loads(raw[i:j + 1])
                if isinstance(data, dict) and (data.get("tasks")
                                               or data.get("phases")):
                    return self._normalise_plan(data)
            except json.JSONDecodeError:
                pass
        return {}

    def _normalise_plan(self, data: dict) -> dict:

        phases = []
        for i, p in enumerate(data.get("tasks") or data.get("phases") or [],
                              start=1):
            if not isinstance(p, dict):
                continue
            files = []
            for f in p.get("files", []):
                if isinstance(f, str):
                    files.append({"path": f, "purpose": "",
                                  "kind": self._infer_kind(f)})
                elif isinstance(f, dict) and f.get("path"):
                    kind = str(f.get("kind", "")).strip().lower()
                    if kind not in ("server", "client", "route"):
                        kind = self._infer_kind(f["path"])
                    # The page's brief, kept whole.
                    lst = lambda v: [str(x).strip() for x in (v or [])
                                     if str(x).strip()] if isinstance(v, list) else []
                    files.append({"path": f["path"],
                                  "purpose": f.get("purpose", ""),
                                  "kind": kind,
                                  "reads": lst(f.get("reads"))[:12],
                                  "writes": lst(f.get("writes"))[:12],
                                  "sections": lst(f.get("sections"))[:10],
                                  "actions": lst(f.get("actions"))[:10],
                                  "invariants": lst(f.get("invariants"))[:10]})

            raw = p.get("covers") or []
            if isinstance(raw, str):
                raw = re.split(r"[,;]\s*|\s+", raw)
            covers = [str(c).strip() for c in raw if str(c).strip()]

            known = getattr(self, "_known_fr", None)
            if known:
                invented = [c for c in covers if c.upper().startswith("FR-")
                            and c.upper() not in known]
                if invented:
                    covers = [c for c in covers if c not in invented]
                    self._log("INFO", "   ⚠ dropped invented requirement ids "
                                      f"from a task: {', '.join(invented)}")
            phases.append({
                "id": p.get("id", i),
                "title": p.get("title", f"Task {i}"),
                "goal": p.get("goal", ""),
                "done_when": p.get("done_when", ""),
                "covers": covers,
                "files": files,
            })
        data["phases"] = [p for p in phases if p["files"]]

        if data["phases"]:
            planned = {f["path"] for p in data["phases"] for f in p["files"]}
            if not planned & {"app/page.jsx", "app/page.js"}:
                data["phases"][0]["files"].insert(0, {
                    "path": "app/page.jsx",
                    "purpose": "The home page — the first thing anyone sees. "
                               "AgentForge scaffolds a placeholder here that says "
                               "'Building…'; this replaces it.",
                    "kind": "server"})
                self._log("WARN", "   📋 The plan did not include app/page.jsx "
                                  "— added it, or the app would ship the "
                                  "'Building…' placeholder as its home page")

        deps = [d for d in data.get("dependencies", []) if isinstance(d, str)]
        data["dependencies"] = deps
        data["source_requirements"] = [
            " ".join(str(x).split())[:320]
            for x in (data.get("source_requirements") or [])
            if str(x).strip()
        ][:24]

        images = []
        for im in data.get("images") or []:
            if not isinstance(im, dict) or not im.get("prompt"):
                continue
            key = re.sub(r"[^a-z0-9-]+", "-",
                         str(im.get("key") or "image").lower()).strip("-") or "image"
            prompt = str(im["prompt"])[:400]
            aspect = str(im.get("aspect", "landscape")).lower()
            try:
                count = int(im.get("count") or 1)
            except (TypeError, ValueError):
                count = 1
            count = max(1, min(count, 24))
            if count == 1:
                images.append({"key": key, "prompt": prompt, "aspect": aspect})
                continue
            # A catalogue wants a photo per row
            variants = [v.strip() for v in (im.get("variants") or [])
                        if str(v).strip()][:count]
            for n in range(1, count + 1):
                hint = variants[n - 1] if n <= len(variants) else ""
                images.append({
                    "key": f"{key}-{n}",
                    "prompt": (f"{prompt}, {hint}" if hint else
                               f"{prompt}, variation {n}")[:400],
                    "aspect": aspect,
                })

        seen, unique = set(), []
        for im in images:
            if im["key"] in seen:
                continue
            seen.add(im["key"])
            unique.append(im)
        data["images"] = unique[:48]

        accounts = []
        for a in data.get("demo_accounts") or []:
            if isinstance(a, dict) and a.get("email") and a.get("password"):
                accounts.append({"email": a["email"], "password": a["password"],
                                 "role": a.get("role", "user")})
        data["demo_accounts"] = accounts

        capabilities = []
        for i, c in enumerate(data.get("capabilities") or [], start=1):
            if not isinstance(c, dict):
                continue
            req = str(c.get("requirement") or c.get("text") or "").strip()
            proof = str(c.get("proof") or "").strip()
            if not req:
                continue
            raw_files = c.get("files") or []
            cfiles = [str(x).strip().lstrip("./").replace("\\", "/")
                      for x in raw_files if str(x).strip()] \
                if isinstance(raw_files, list) else []
            cid = str(c.get("id") or f"CAP-{i:03d}").strip().upper()
            if not re.match(r"^CAP-[A-Z0-9_-]+$", cid):
                cid = f"CAP-{i:03d}"
            capabilities.append({
                "id": cid[:40],
                "who": str(c.get("who") or "").strip()[:60],
                "requirement": req[:320],
                "proof": proof[:500],
                "files": cfiles[:10],
                "e2e": bool(c.get("e2e", True)),
            })
        data["capabilities"] = capabilities[:40]

        # Structured cross-task memory.
        workflows = []
        for w in data.get("workflows") or []:
            if not isinstance(w, dict):
                continue
            steps = [str(x).strip() for x in (w.get("steps") or [])
                     if str(x).strip()] if isinstance(w.get("steps"), list) else []
            raw_covers = w.get("covers") or []
            covers = [str(x).strip().upper() for x in raw_covers
                      if str(x).strip()] if isinstance(raw_covers, list) else []
            workflows.append({
                "name": str(w.get("name") or "workflow").strip()[:80],
                "who": str(w.get("who") or "").strip()[:60],
                "covers": covers[:20],
                "steps": steps[:12],
            })
        data["workflows"] = workflows[:12]

        contracts = []
        allowed_kinds = {"api", "navigation"}
        allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        for c in data.get("contracts") or []:
            if not isinstance(c, dict):
                continue
            src = str(c.get("from") or "").replace("\\", "/").strip().lstrip("./")
            target = str(c.get("target") or "").strip()
            kind = str(c.get("kind") or "").strip().lower()
            if kind not in allowed_kinds or not src or not target.startswith("/"):
                continue
            method = str(c.get("method") or "").upper()
            if kind == "api" and method and method not in allowed_methods:
                method = ""
            fields = lambda key: [str(x).strip() for x in (c.get(key) or [])
                                  if str(x).strip()][:16] \
                if isinstance(c.get(key), list) else []
            contracts.append({
                "name": str(c.get("name") or f"{kind}-{len(contracts)+1}").strip()[:80],
                "kind": kind,
                "from": src,
                "target": target[:180],
                "method": method if kind == "api" else "",
                "request": fields("request"),
                "response": fields("response"),
                "trigger": str(c.get("trigger") or "").strip()[:240],
                "effect": str(c.get("effect") or "").strip()[:320],
            })
        # If a planner omits a contract.
        seen_contracts = {(c["from"], c["target"], c["kind"], c.get("method") or "")
                          for c in contracts}
        for phase in data.get("phases") or []:
            for f in phase.get("files") or []:
                src = f.get("path", "")
                for action in f.get("actions") or []:
                    for target in self._ROUTE_TOKEN.findall(str(action)):
                        target = target.rstrip(".,;:)") or "/"
                        kind = "api" if target.startswith("/api") else "navigation"
                        mm = re.search(r"\b(GET|POST|PUT|PATCH|DELETE)\b", str(action), re.I)
                        method = mm.group(1).upper() if mm and kind == "api" else ""
                        key = (src, target, kind, method)
                        # When the action does not name a method.
                        if key in seen_contracts or (not method and any(
                                k[:3] == key[:3] for k in seen_contracts)):
                            continue
                        seen_contracts.add(key)
                        contracts.append({
                            "name": f"auto-{Path(src).stem}-{len(contracts)+1}",
                            "kind": kind,
                            "from": src,
                            "target": target,
                            "method": method,
                            "request": [],
                            "response": [],
                            "trigger": str(action)[:240],
                            "effect": str(action)[:320],
                        })
        data["contracts"] = contracts[:60]

        data["signup_role"] = str(data.get("signup_role") or "").strip()
        return data

    @staticmethod
    def _infer_kind(path: str) -> str:
        """Fallback when the plan omits `kind` — route handlers are obvious."""
        if re.match(r"^app/.*route\.jsx?$", path or ""):
            return "route"
        return "server"
