"""Code search, route mapping, decision parsing and fixed fallback reasoning."""
from .debugger_common import *


class DebuggerReasoningMixin:
    @staticmethod
    def _clean_path(arg: str) -> str:
        return str(arg or "").strip().lstrip("./").replace("\\", "/")

    def _search_code(self, query: str) -> str:
        files = getattr(self.arch, "files", None) or {}
        q = str(query or "").strip()
        if not q:
            return "empty search"
        try:
            rx = re.compile(q, re.I)
        except re.error:
            rx = re.compile(re.escape(q), re.I)
        rows = []
        for rel, body in files.items():
            if not rel.startswith(("app/", "components/", "lib/")):
                continue
            for n, line in enumerate(str(body or "").splitlines(), 1):
                if rx.search(line):
                    rows.append(f"{rel}:{n}: {line.strip()[:240]}")
                    if len(rows) >= 50:
                        return "\n".join(rows)
        return "\n".join(rows) or "no matches"

    def _route_source(self, route: str) -> str:
        files = getattr(self.arch, "files", None) or {}
        route = str(route or "").strip()
        if not route.startswith("/"):
            return ""
        clean = route.split("?", 1)[0].rstrip("/") or "/"
        if clean.startswith("/api/"):
            segs = [s for s in clean[len("/api/"):].split("/") if s]
            exact = "app/api/" + "/".join(segs) + "/route.js"
            if exact in files:
                return exact
            return self._match_dynamic("app/api", segs, "route.js", files)
        try:
            rel = self.agent._page_for(clean)
            if rel in files:
                return rel
        except Exception:
            pass
        segs = [s for s in clean.strip("/").split("/") if s]
        exact = "app/page.jsx" if not segs else "app/" + "/".join(segs) + "/page.jsx"
        if exact in files:
            return exact
        return self._match_dynamic("app", segs, "page.jsx", files)

    @staticmethod
    def _match_dynamic(prefix: str, segs: list[str], leaf: str, files: dict) -> str:
        candidates = []
        for rel in files:
            if not rel.startswith(prefix + "/") or not rel.endswith("/" + leaf):
                continue
            middle = rel[len(prefix) + 1: -(len(leaf) + 1)]
            parts = middle.split("/") if middle else []
            if len(parts) != len(segs):
                continue
            ok = all(a == b or (a.startswith("[") and a.endswith("]"))
                     for a, b in zip(parts, segs))
            if ok:
                candidates.append(rel)
        return sorted(candidates)[0] if candidates else ""

    def _analyzer(self, arg: str) -> str:
        if not self.analyzer:
            return "analyzer unavailable"
        target = self._clean_path(arg)
        try:
            report = self.analyzer.scan()
        except Exception as e:
            return f"analyzer failed: {e}"
        rows = []
        for f in getattr(report, "findings", None) or []:
            paths = {str(getattr(f, "path", "") or "")}
            paths |= {str(x or "") for x in (getattr(f, "extra", None) or [])}
            if target and target not in paths:
                continue
            rows.append(f"[{getattr(f, 'code', 'FINDING')}] {getattr(f, 'path', '')}: {getattr(f, 'message', '')}")
            if len(rows) >= 20:
                break
        return "\n".join(rows) or "no analyzer findings for that target"

    @staticmethod
    def _line(raw: str, name: str, default="") -> str:
        m = re.search(rf"^\s*{re.escape(name)}\s*::\s*(.*?)\s*$",
                      str(raw or ""), re.I | re.M)
        return m.group(1).strip() if m else default

    def _parse_decision(self, raw: str, failures) -> dict:
        text = str(raw or "").strip()
        obj = {}
        # Accept strict lines first.
        try:
            if text.startswith("{") and text.endswith("}"):
                obj = json.loads(text)
        except Exception:
            obj = {}

        verdict = str(self._line(text, "VERDICT", obj.get("verdict", "UNKNOWN"))).upper().strip()
        if verdict not in VERDICTS:
            hit = re.search(r"\b(TEST_FIX|APP_FIX|AUTH_FIX|DATA_PREREQUISITE|BLOCKED_UPSTREAM|ROUTE_FIX|RETRY_TRANSIENT|UNKNOWN)\b", text, re.I)
            verdict = hit.group(1).upper() if hit else "UNKNOWN"
        file_text = self._line(text, "FILES", "") or obj.get("files", "")
        if isinstance(file_text, list):
            raw_files = file_text
        else:
            raw_files = re.split(r"\s*,\s*", str(file_text))
        files = []
        for rel in raw_files:
            rel = self._clean_path(rel)
            if rel and rel.upper() != "NONE" and rel.startswith(("app/", "components/", "lib/")):
                files.append(rel)
        patch = self._line(text, "TEST_PATCH", "") or str(obj.get("test_patch") or "")
        if patch.upper() == "NONE":
            patch = ""
        step = self._line(text, "TEST_STEP", "") or str(obj.get("test_step") or "")
        if step.upper() == "NONE":
            step = ""
        root = self._line(text, "ROOT", "") or str(obj.get("root") or "")
        hyp = self._line(text, "HYPOTHESIS", "") or str(obj.get("hypothesis") or "")
        nxt = self._line(text, "NEXT", "") or str(obj.get("next") or "")
        if not root and verdict != "UNKNOWN":
            m = re.search(rf"\b{re.escape(verdict)}\b\s*[:\-]\s*(.+)", text, re.I)
            if m:
                root = m.group(1).splitlines()[0].strip()
        return {
            "verdict": verdict, "root": root[:700],
            "files": list(dict.fromkeys(files))[:4],
            "test_step": step[:800], "test_patch": patch[:1200],
            "hypothesis": hyp[:700], "next": nxt[:900],
            "confidence": str(self._line(text, "CONFIDENCE", obj.get("confidence", "low"))).lower(),
            "privileged": [],
        }

    @staticmethod
    def _semantic_words(text: str) -> set[str]:
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
        raw = re.findall(r"[a-z0-9]+", text.lower())
        stop = {"nothing","matched","button","role","text","field","testid",
                "selector","assertion","expected","wait","for","name","the",
                "and","with","from","this","that","click","clicking"}
        words = {x for x in raw if len(x) >= 3 and x not in stop}
        low = str(text or "").lower()
        if "★" in str(text or "") or "star" in low:
            words |= {"star", "rating", "review"}
        # Only the pairs that actually change a word. Everything else is
        # normalised by shape, so any app's nouns collapse the same way.
        aliases = {"catalog": "catalogue", "rate": "rating", "rated": "rating",
                   "reviews": "review", "categories": "category"}
        return {aliases.get(x, singular(x)) for x in words}

    def _plan_text_for_goal(self) -> str:
        plan = getattr(self.arch, "plan", None) or {}
        goal = _norm(self.notebook.goal)
        rows = []
        covers = set()
        for w in plan.get("workflows") or []:
            if not isinstance(w, dict):
                continue
            name = _norm(w.get("name") or w.get("title") or "")
            if not goal or goal in name or name in goal:
                rows.append(json.dumps(w, ensure_ascii=False))
                covers |= {str(x).upper() for x in (w.get("covers") or [])}
        if not rows and plan.get("workflows"):
            # Goal wording can be shortened in the UI.
            rows.extend(json.dumps(w, ensure_ascii=False) for w in (plan.get("workflows") or []) if isinstance(w, dict))
        for c in plan.get("capabilities") or []:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id") or "").upper()
            if not covers or cid in covers:
                rows.append(json.dumps(c, ensure_ascii=False))
        return "\n".join(rows)[:14000]

    def _selector_source_candidates(self, failure, words: set[str]) -> list[str]:
        files = getattr(self.arch, "files", None) or {}
        target = str(getattr(failure, "target", "") or "")
        candidates = []
        if target in files:
            candidates.append(target)
            body = str(files.get(target) or "")
            for kind, name in re.findall(r"from\s+['\"]@/(components|lib)/([\w./-]+)['\"]", body):
                base = f"{kind}/{name}"
                for ext in (".jsx", ".js", ""):
                    rel = base + ext
                    if rel in files:
                        candidates.append(rel); break
        scored = []
        for rel in dict.fromkeys(candidates):
            body = str(files.get(rel) or "")
            got = self._semantic_words(body + " " + rel.replace('/', ' ').replace('-', ' '))
            overlap = len(words & got)
            bonus = 1 if rel.startswith("components/") else 0
            scored.append((overlap, bonus, rel))
        scored.sort(reverse=True)
        return [r for score, _bonus, r in scored if score > 0][:3] or ([target] if target else [])

    @staticmethod
    def _selector_intent(failure) -> tuple[str, set[str]]:
        """Return the selector channel and business words from the failed locator."""
        msg = str(getattr(failure, "message", "") or "")

        m = re.search(r"nothing\s+matched\s+field\s+['\"]([^'\"]+)['\"]", msg, re.I)
        if not m:
            m = re.search(r"\bfield\s*=\s*['\"]?([a-z0-9 _.-]+)", msg, re.I)
        if m:
            raw = m.group(1).strip().lower()
            words = {x for x in re.findall(r"[a-z0-9]+", raw) if len(x) >= 2}
            if raw == "name":
                words.add("name")
            return "field", words

        m = re.search(r"nothing\s+matched\s+[a-z0-9_-]+\s+role\s+/([^/]+)/[a-z]*", msg, re.I)
        if not m:
            m = re.search(r"\brole\s*(?:=|:)\s*/([^/]+)/[a-z]*", msg, re.I)
        if m:
            raw = m.group(1).replace("\\s", " ")
            return "control", DebuggerReasoningMixin._semantic_words(raw)

        m = re.search(r"nothing\s+matched\s+testid\s+['\"]([^'\"]+)['\"]", msg, re.I)
        if not m:
            m = re.search(r"\btestid\s*(?:=|:)\s*['\"]?([^'\"\s]+)", msg, re.I)
        if m:
            return "control", DebuggerReasoningMixin._semantic_words(m.group(1))

        return "generic", DebuggerReasoningMixin._semantic_words(msg)

    @staticmethod
    def _selector_dom_excerpt(packet: str, channel: str) -> str:
        """Limit arbitration to evidence that can actually satisfy the locator."""
        evidence = str(packet or "")
        if "## DOM at failure" in evidence:
            dom = evidence.split("## DOM at failure", 1)[1]
            dom = re.split(r"\n##\s+", dom, maxsplit=1)[0]
        else:
            dom = evidence
        lines = dom.splitlines()
        if channel == "field":
            chosen = [line for line in lines if re.match(r"\s*fields\s*:", line, re.I)]
        elif channel == "control":
            chosen = [line for line in lines if re.match(r"\s*controls\s*:", line, re.I)]
        else:
            chosen = lines
        return "\n".join(chosen)[:9000]

    @staticmethod
    def _field_required_by_plan(field_words: set[str], plan_text: str) -> bool:
        """Conservative requirement check for form-field selectors."""
        low = str(plan_text or "").lower()
        if not field_words:
            return False
        if field_words == {"name"}:
            return bool(re.search(
                r"\b(?:full\s+name|first\s+name|last\s+name|name\s+field|"
                r"enter\s+(?:your\s+)?name|provide\s+(?:your\s+)?name)\b", low))
        # Strip JSON/property keys so only requirement values
        values = re.sub(r'"[a-z0-9_-]+"\s*:', ' ', low)
        return bool(field_words & DebuggerReasoningMixin._semantic_words(values))

    def _selector_arbitration(self, failures, packet: str) -> dict | None:
        if not failures or not all(str(getattr(f, "kind", "")).upper() in ("SELECTOR", "ASSERTION") for f in failures):
            return None
        f0 = failures[0]
        channel, words = self._selector_intent(f0)
        plan_text = self._plan_text_for_goal()
        plan_words = self._semantic_words(plan_text)
        dom_part = self._selector_dom_excerpt(packet, channel)
        dom_words = self._semantic_words(dom_part)
        if channel == "field":
            dom_words |= {x for x in words if re.search(
                rf"\b(?:name|id|label|placeholder|aria|autocomplete)\s*=\s*[^|\n]*\b{re.escape(x)}\b",
                dom_part, re.I)}

        if words and len(words & dom_words) >= max(1, min(2, len(words))):
            return {"verdict":"TEST_FIX",
                    "root":"the rendered DOM contains the required action, but the authored selector does not target its observed accessible name/testid",
                    "files":[], "test_step":"", "test_patch":"",
                    "hypothesis":"replace only the failing locator with the observed DOM control",
                    "next":"patch one scenario step using the observed control and resume the checkpoint",
                    "confidence":"high", "privileged":[]}

        required = (self._field_required_by_plan(words, plan_text)
                    if channel == "field" else bool(words and (words & plan_words)))
        # Star/rating is a common accessibility trap.
        if {"star","rating","review"} & words and {"rating","review"} & plan_words:
            required = True
        if required:
            files = self._selector_source_candidates(f0, words | (words & plan_words))
            return {"verdict":"APP_FIX",
                    "root":"the accepted workflow requires this business action, but no semantically equivalent accessible control is present in the failure DOM",
                    "files":files[:3], "test_step":"", "test_patch":"",
                    "hypothesis":"the production UI omitted the required control or rendered it without an accessible functional name",
                    "next":"inspect the owning component and implement/label the required action without changing the workflow",
                    "confidence":"high", "privileged":[]}
        return {"verdict":"TEST_FIX",
                "root":"the selector/assertion is not backed by the accepted workflow and no matching product requirement was found",
                "files":[], "test_step":"", "test_patch":"",
                "hypothesis":"the scenario invented a locator rather than following observed UI evidence",
                "next":"patch only the failing scenario step from the DOM evidence",
                "confidence":"medium", "privileged":[]}

    @staticmethod
    def _successful_mutation(text: str) -> bool:
        return bool(re.search(
            r"(?:POST|PUT|PATCH|DELETE)\s+https?://[^\s]+/api/[^\s]+.*?HTTP\s+2\d\d|"
            r"HTTP\s+2\d\d\s+(?:POST|PUT|PATCH|DELETE)\s+https?://",
            str(text or ""), re.I | re.S))

    def _fallback(self, failures, *, packet: str = "", raw: str = "") -> dict:
        blob = "\n".join(str(getattr(f, "message", "") or "") + "\n" +
                         str(getattr(f, "stack", "") or "") for f in failures)
        evidence = blob + "\n" + str(packet or "") + "\n" + str(raw or "")
        files = []
        api500 = re.search(r"HTTP\s+5\d\d\s+(?:GET|POST|PUT|PATCH|DELETE)\s+https?://[^/]+(/api/[^\s—]+)", evidence, re.I)
        if not api500:
            api500 = re.search(r"(?:GET|POST|PUT|PATCH|DELETE)\s+(/api/[^\s]+)\s+5\d\d", evidence, re.I)
        if api500:
            route = api500.group(1).split("?", 1)[0].rstrip(".,;)")
            rel = self._route_source(route)
            if rel:
                files = [rel]
            verdict, root = "APP_FIX", f"{route} returned a server 5xx during the required journey"
        elif re.search(r"HTTP\s+401\b|unauthori[sz]ed|no authenticated browser session", evidence, re.I):
            verdict, root = "AUTH_FIX", "authenticated workflow lost its session at a protected boundary"
        elif re.search(r"Event handlers cannot be passed to Client Component props|is a Server Component", evidence, re.I):
            verdict, root = "APP_FIX", "Next.js reported a server/client component boundary runtime error"
        elif failures and all(str(getattr(f, "kind", "")).upper() in ("SELECTOR", "ASSERTION") for f in failures):
            arb = self._selector_arbitration(failures, packet)
            if arb:
                return arb
            verdict, root = "TEST_FIX", "scenario selector/assertion is not grounded in the rendered page"
        elif self._successful_mutation(evidence):
            f0 = failures[0] if failures else None
            target = str(getattr(f0, "target", "") or "") if f0 else ""
            verdict, root = "APP_FIX", "the required mutation succeeded, but the downstream UI/state proof did not materialize"
            if target:
                files = [target]
        elif re.search(r"ERR_ABORTED|destination stream closed early|Fast Refresh had to perform a full reload", evidence, re.I):
            verdict, root = "RETRY_TRANSIENT", "navigation/stream failure is consistent with a transient Next dev compile"
        else:
            verdict, root = "UNKNOWN", "model/tool loop did not establish a safe root cause"
        if not files:
            target = str(getattr(failures[0], "target", "") or "") if failures else ""
            if target.startswith(("app/", "components/", "lib/")) and verdict in ("APP_FIX", "ROUTE_FIX"):
                files = [target]
        return {"verdict": verdict, "root": root, "files": files, "test_step": "",
                "test_patch": "", "hypothesis": root,
                "next": "apply the smallest evidence-scoped repair and rerun the failing checkpoint",
                "confidence": "medium" if verdict != "UNKNOWN" else "low", "privileged": []}
