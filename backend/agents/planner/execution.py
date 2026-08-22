"""Requirement extraction, capability-gap detection and plan generation."""
from agents.builder.orchestration.common import *
from agents.planner.capability_recovery import CapabilityRecoveryMixin


class ArchitectPlanningMixin(CapabilityRecoveryMixin):
    PLANNER_TIMEOUT = 300
    REPLAN_TIMEOUT = 180
    PLANNER_OUTPUT_TOKENS = 16_000
    CAPABILITY_REPLAN_ROUNDS = 6

    _CAPABILITY_STOP = {
        "with", "from", "that", "this", "into", "their", "every", "using",
        "allows", "ability", "engine", "management", "system", "feature",
        "users", "user", "interface", "instant", "instantly",
        "administrative", "secure", "automatic", "current", "maintenance",
        "generation", "processing", "based",
    }
    # Product briefs and plans often use different words for the same job.
    # Collapse those words before the mechanical completeness comparison.
    _CAPABILITY_ALIASES = {
        "authentication": "identity", "authenticate": "identity",
        "auth": "identity", "login": "identity", "signin": "identity",
        "admin": "admin", "administrator": "admin",
        "administrators": "admin", "moderator": "admin",
        "rbac": "authorization", "role": "authorization",
        "roles": "authorization", "permission": "authorization",
        "permissions": "authorization", "access": "authorization",
        "sale": "sales", "sales": "sales", "pos": "sales",
        "checkout": "sales", "terminal": "sales",
        "barcode": "barcode", "scan": "barcode", "scanner": "barcode",
        "cart": "cart", "basket": "cart",
        "tax": "totals", "discount": "totals", "total": "totals",
        "totals": "totals", "calculation": "totals", "calculate": "totals",
        "payment": "payment", "payments": "payment", "paid": "payment",
        "pay": "payment", "cash": "payment", "card": "payment",
        "bill": "billing", "bills": "billing", "billing": "billing",
        "invoice": "billing", "invoices": "billing", "receipt": "billing",
        "receipts": "billing", "issuance": "billing", "printing": "print",
        "printed": "print", "print": "print",
        "stock": "inventory", "inventory": "inventory",
        "warehouse": "warehouse", "warehouses": "warehouse",
        "reorder": "reorder", "lowstock": "reorder", "alert": "reorder",
        "alerts": "reorder", "grn": "receiving", "goods": "receiving",
        "received": "receiving", "receiving": "receiving",
        "customer": "customer", "customers": "customer",
        "client": "customer", "clients": "customer", "profile": "profile",
        "profiles": "profile", "credit": "credit", "balance": "credit",
        "balances": "credit", "debt": "credit",
        "price": "pricing", "prices": "pricing", "pricing": "pricing",
        "rate": "pricing", "rates": "pricing", "cost": "pricing",
        "costs": "pricing", "fee": "pricing", "fees": "pricing",
        "available": "availability", "availability": "availability",
        "vacancy": "availability", "vacancies": "availability",
        "approve": "approval", "approved": "approval",
        "approval": "approval", "confirm": "approval",
        "confirmed": "approval", "accept": "approval",
    }
    _INERT_PLAN_RE = re.compile(
        r"\bplaceholder\s+(?:page|screen|section|component|content|"
        r"implementation|only)\b"
        r"|\b(?:for\s+now|initially)\s+a\s+placeholder\b"
        r"|\bdisabled\s+for\s+now\b"
        r"|\bdisabled\s+until\s+[\w\s]{0,40}?\b(?:is|are)\s+"
        r"(?:built|implemented|added|ready|done)\b"
        r"|\b(?:coming\s+soon|under\s+construction|todo|"
        r"not\s+implemented|future\s+work|"
        r"stub(?:bed)?\s+(?:button|action|control|page))\b", re.I)

    def _planning_output_limit(self):
        """Cloud answers need a ceiling; preserve local-model behavior."""
        return (self.PLANNER_OUTPUT_TOKENS
                if getattr(self, "planner_is_cloud", False) else None)

    @staticmethod
    def _source_requirements(user_prompt: str) -> list:
        """Exact action clauses from a free-form user idea."""
        text = " ".join(str(user_prompt or "").replace("\r", " ").split())
        if not text:
            return []
        action = re.compile(
            r"\b(?:browse(?:s|d|ing)?|check(?:s|ed|ing)?|book(?:s|ed|ing)?|"
            r"see(?:s|ing)?|view(?:s|ed|ing)?|list(?:s|ed|ing)?|search(?:es|ed|ing)?|"
            r"filter(?:s|ed|ing)?|create(?:s|d|ing)?|add(?:s|ed|ing)?|edit(?:s|ed|ing)?|"
            r"update(?:s|d|ing)?|change(?:s|d|ing)?|delete(?:s|d|ing)?|remove(?:s|d|ing)?|"
            r"mark(?:s|ed|ing)?|pay(?:s|ing|paid)?|track(?:s|ed|ing)?|manage(?:s|d|ing)?|"
            r"assign(?:s|ed|ing)?|approve(?:s|d|ing)?|reject(?:s|ed|ing)?|upload(?:s|ed|ing)?|"
            r"download(?:s|ed|ing)?|register(?:s|ed|ing)?|sign\s*[- ]?in|log\s*[- ]?in|"
            r"login|schedule(?:s|d|ing)?|reserve(?:s|d|ing)?|cancel(?:s|led|ing)?|"
            r"seed(?:s|ed|ing)?|show(?:s|ed|ing)?|set(?:s|ting)?|send(?:s|ing)?|"
            r"receive(?:s|d|ing)?|export(?:s|ed|ing)?|import(?:s|ed|ing)?)\b", re.I)
        actor_rx = re.compile(
            r"\b(?:guest|admin(?:istrator)?|customer|client|user|member|"
            r"manager|owner|staff|employee|seller|buyer|visitor|organizer|"
            r"teacher|student|doctor|patient|librarian|technician|agent|"
            r"\w{3,}(?:ist|ian|eer|ator|isor|iser|izer|keeper|master|"
            r"officer|ician|ent|ee))\b", re.I)
        # Half the verbs above are also ordinary nouns -- book, list
        determiners = {"a", "an", "the", "this", "that", "these", "those",
                       "its", "his", "her", "their", "our", "your", "my",
                       "each", "every", "some", "any", "no", "another", "per",
                       "one", "two", "three", "four", "five", "several"}

        def verbal(text: str) -> bool:
            """True when this clause contains an action word used AS a verb."""
            text = str(text or "")
            for m in action.finditer(text):
                start, end = m.span()
                if (start and text[start - 1] == "-") or \
                        (end < len(text) and text[end] == "-"):
                    continue
                prev = re.search(r"([A-Za-z0-9']+)\s*$", text[:start])
                if prev and prev.group(1).lower() in determiners:
                    continue
                return True
            return False

        out = []

        def action_parts(sentence):
            # Split ONLY when the next phrase has its own action verb.
            comma_chunks = [x.strip() for x in re.split(r"\s*,\s*", sentence) if x.strip()]
            chunks = []
            for ch in comma_chunks:
                if chunks and not verbal(ch):
                    chunks[-1] += ", " + ch
                else:
                    chunks.append(ch)
            final = []
            for ch in chunks:
                bits, start = [], 0
                joins = list(re.finditer(r"\s+(?:and|then)\s+", ch, re.I))
                for j, m in enumerate(joins):
                    end = joins[j + 1].start() if j + 1 < len(joins) else len(ch)
                    immediate_right = ch[m.end():end]
                    if verbal(immediate_right):
                        piece = ch[start:m.start()].strip()
                        if piece:
                            bits.append(piece)
                        start = m.end()
                tail = ch[start:].strip()
                if tail:
                    bits.append(tail)
                final.extend(bits)
            return final

        for sent in re.split(r"(?<=[.!?])\s+|[;\n]+", text):
            sent = sent.strip(" .")
            if not sent:
                continue
            actor = ""
            for bit in action_parts(sent):
                bit = bit.strip(" .")
                if not bit or not verbal(bit):
                    continue
                local_actor = actor_rx.search(bit)
                if local_actor:
                    actor = local_actor.group(0).lower()
                req = bit
                if actor and not local_actor:
                    req = f"{actor} {bit}"
                req = " ".join(req.split())[:320]
                if req and req.lower() not in {x.lower() for x in out}:
                    out.append(req)
                if len(out) >= 24:
                    return out
        return out

    @staticmethod
    def _core_features(markdown: str) -> list:
        """Core-feature bullets from the markdown half of the plan."""
        md = markdown or ""
        m = re.search(r"(?ms)^## Core Features\s*$\n(.*?)(?=^## \S|\Z)", md)
        if not m:
            return []
        out = []
        for line in m.group(1).splitlines():
            x = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
            if x:
                text = re.sub(r"[*_`]", "", x.group(1)).strip()
                if text:
                    out.append(text[:400])
        return out[:30]

    def _app_nouns(self) -> set:
        """The short words THIS app is built on, so they are not filtered out."""
        cached = getattr(self, "_app_noun_cache", None)
        if cached is not None:
            return cached
        out = set()
        try:
            v = self.vocab
            for word in (v.thing, v.things, v.child, v.children, v.actor):
                if word:
                    out.add(str(word).lower())
        except Exception as e:                                  # noqa: BLE001
            log.debug(f"app nouns unavailable: {e}")
        self._app_noun_cache = out
        return out

    def _capability_words(self, text: str) -> set:
        """Semantic terms used by both gap detection and deterministic repair."""
        normalized = str(text or "").lower()
        normalized = re.sub(r"\b(?:signs?|logs?)\s*[- ]\s*in\b",
                            " signin ", normalized)
        normalized = re.sub(r"\blow\s*[- ]\s*stock\b", " lowstock ", normalized)
        raw = re.findall(r"[a-z0-9]+", normalized)
        short_ok = set(self._CAPABILITY_ALIASES) | self._app_nouns()
        out = set()
        for word in raw:
            if word in self._CAPABILITY_STOP:
                continue
            if len(word) < 5 and word not in short_ok:
                continue
            if word.endswith("ies") and len(word) > 4:
                word = word[:-3] + "y"
            elif word.endswith("ing") and len(word) > 5:
                word = word[:-3]
                if word.endswith(word[-1:] * 2):
                    word = word[:-1]
            elif word.endswith("ed") and len(word) > 4:
                word = word[:-2]
            elif word.endswith("es") and len(word) > 4:
                word = word[:-1] if word[-3] in "sgcz" else word[:-2]
            elif word.endswith("s") and len(word) > 4:
                word = word[:-1]
            out.add(self._CAPABILITY_ALIASES.get(word, word))
        return out

    def _capability_gaps(self, plan: dict, markdown: str) -> tuple:
        """(core features with no capability, e2e capabilities with no workflow)."""
        # Replan prose is allowed to improve, but it cannot erase a Core
        # Feature from the authoritative brief. Keep that original ledger in
        # every later comparison.
        source_features = list(getattr(self, "_source_core_features", None) or [])
        features = list(dict.fromkeys(source_features + self._core_features(markdown)))
        caps = [c for c in (plan or {}).get("capabilities") or [] if isinstance(c, dict)]
        cap_words = [self._capability_words(str(c.get("requirement") or "") + " " +
                                            str(c.get("proof") or "") + " " +
                                            str(c.get("who") or "")) for c in caps]
        missing_features = []
        for feat in features:
            fw = self._capability_words(feat)
            if not fw:
                continue
            label = str(feat).split(":", 1)[0]
            anchors = self._capability_words(label)
            if not any((anchors & cw) or len(fw & cw) >= min(2, len(fw))
                       for cw in cap_words):
                missing_features.append(feat)

        # Independent source ledger for free-form ideas.
        for req in (plan or {}).get("source_requirements") or []:
            rw = self._capability_words(str(req))
            if not rw:
                continue
            need = 1 if len(rw) == 1 else 2
            if not any(len(rw & cw) >= need for cw in cap_words):
                missing_features.append("SOURCE: " + str(req)[:280])

        covered = set()
        for w in (plan or {}).get("workflows") or []:
            for cid in (w.get("covers") or []) if isinstance(w, dict) else []:
                covered.add(str(cid).strip().upper())
        unwalked = [c.get("id") for c in caps
                    if c.get("e2e", True) and c.get("id") and
                    str(c.get("id")).upper() not in covered]
        planned = {f.get("path") for ph in (plan or {}).get("phases") or []
                   for f in ph.get("files") or [] if isinstance(f, dict)}
        unmapped = []
        details = []
        for c in caps:
            cid = str(c.get("id") or c.get("requirement") or "capability")
            files = c.get("files") or []
            if not files:
                unmapped.append(cid)
                details.append(f"{cid} lists no files at all — name the exact "
                               f"page/route files that prove it")
                continue
            bad = [x for x in files if x not in planned]
            if bad:
                unmapped.append(cid)
                details.append(
                    f"{cid} names {', '.join(bad[:4])} — no task builds "
                    f"{'those' if len(bad) > 1 else 'that'}. Either add "
                    f"{'them' if len(bad) > 1 else 'it'} to a task's `files`, "
                    f"or point the capability at a file a task does build")

        # A plan can look complete on paper while explicitly scheduling
        for ph in (plan or {}).get("phases") or []:
            if not isinstance(ph, dict):
                continue
            for f in ph.get("files") or []:
                if not isinstance(f, dict):
                    continue
                # Scan the whole file spec, not only `actions`.
                def _spec_text(x):
                    if isinstance(x, dict):
                        return " ".join(_spec_text(v) for v in x.values())
                    if isinstance(x, (list, tuple)):
                        return " ".join(_spec_text(v) for v in x)
                    return str(x or "")
                blob = _spec_text(f)
                hit = self._INERT_PLAN_RE.search(blob)
                if hit:
                    path = str(f.get("path") or "unknown")
                    unmapped.append("INERT-PLAN:" + path)
                    details.append(
                        f"{path} schedules unbuilt work — it says "
                        f"\"{hit.group(0)}\". Plan the real behaviour or "
                        f"remove the file")
        self._last_gap_details = details[:12]
        return missing_features[:12], unwalked[:12], list(dict.fromkeys(unmapped))[:12]

    @staticmethod
    def _requirement_text_map(source: str) -> dict:
        """FR id -> requirement sentence from the SRS handoff."""
        found = {}
        pattern = re.compile(
            r"(?im)^\s*(?:[-*]\s*)?(FR-\d+)\s*[:\-–—]\s*(.+?)\s*$")
        for match in pattern.finditer(str(source or "")):
            rid = match.group(1).upper()
            text = " ".join(match.group(2).split()).strip(" .")
            if text:
                found[rid] = text[:600]
        return found

    def _repair_requirement_coverage(self, plan: dict, missing: list) -> int:
        """Attach a missing FR ledger id when its capability already has files."""
        phases = [p for p in (plan or {}).get("phases") or [] if isinstance(p, dict)]
        caps = [c for c in (plan or {}).get("capabilities") or [] if isinstance(c, dict)]
        texts = getattr(self, "_fr_text", {}) or {}
        if not phases or not caps or not texts:
            return 0

        stop = {"about", "allows", "application", "every", "feature", "from",
                "must", "shall", "should", "system", "that", "their", "these",
                "through", "using", "user", "users", "with"}

        def words(value) -> set:
            out = set()
            for raw in re.findall(r"[a-z0-9]+", str(value or "").lower()):
                if len(raw) < 4 or raw in stop:
                    continue
                word = raw
                if word.endswith("ies") and len(word) > 4:
                    word = word[:-3] + "y"
                elif word.endswith("es") and len(word) > 4:
                    word = word[:-2]
                elif word.endswith("s") and len(word) > 4:
                    word = word[:-1]
                out.add(word)
            return out

        def files(cap) -> set:
            return {str(x.get("path") if isinstance(x, dict) else x).strip()
                    for x in (cap.get("files") or [])
                    if str(x.get("path") if isinstance(x, dict) else x).strip()}

        task_files = [
            {str((f or {}).get("path") or "").strip()
             for f in phase.get("files") or [] if isinstance(f, dict)}
            for phase in phases
        ]
        fixed = 0
        for rid in missing:
            rid = str(rid).upper()
            req_words = words(texts.get(rid))
            if not req_words:
                continue
            number = re.sub(r"\D", "", rid).lstrip("0") or "0"
            ranked = []
            for cap in caps:
                cap_words = words(str(cap.get("requirement") or "") + " " +
                                  str(cap.get("proof") or ""))
                overlap = len(req_words & cap_words)
                cap_number = re.sub(r"\D", "", str(cap.get("id") or "")).lstrip("0") or "0"
                direct = cap_number == number
                if not direct and overlap < min(2, len(req_words)):
                    continue
                ranked.append((100 if direct else overlap, overlap, cap))
            if not ranked:
                continue
            cap = max(ranked, key=lambda item: (item[0], item[1]))[2]
            owned = files(cap)
            homes = [(len(owned & task_files[i]), i) for i in range(len(phases))]
            count, index = max(homes, default=(0, -1))
            if count <= 0:
                continue
            covers = phases[index].setdefault("covers", [])
            if rid not in {str(x).strip().upper() for x in covers}:
                covers.append(rid)
                fixed += 1
                self._log("INFO", f"   🧩 {rid} already has capability files — "
                                  f"attached its ledger id to task {phases[index].get('id', index + 1)}")
        return fixed

    def _repair_capability_map(self, plan: dict) -> int:
        """Close the capability gaps that need no judgement."""
        phases = [p for p in (plan or {}).get("phases") or [] if isinstance(p, dict)]
        caps = [c for c in (plan or {}).get("capabilities") or [] if isinstance(c, dict)]
        if not phases or not caps:
            return 0
        planned = {f.get("path") for ph in phases
                   for f in ph.get("files") or [] if isinstance(f, dict)}

        def home_for(path: str) -> dict:
            """The task this file most obviously belongs to."""
            folder = str(path).rsplit("/", 1)[0]
            best, score = None, -1
            for ph in phases:
                files = [str(f.get("path") or "") for f in ph.get("files") or []
                         if isinstance(f, dict)]
                if not files:
                    continue
                # Same folder first, then same top-level area, then any task.
                s = sum(3 if f.rsplit("/", 1)[0] == folder else
                        1 if f.split("/")[:2] == str(path).split("/")[:2] else 0
                        for f in files)
                if s > score:
                    best, score = ph, s
            return best or phases[-1]

        # Routes a workflow walks, so a capability with no files
        route_file = {}
        for ph in phases:
            for f in ph.get("files") or []:
                rel = str((f or {}).get("path") or "")
                m = re.match(r"^app/(.*/)?page\.(?:jsx|js)$", rel)
                if m:
                    seg = re.sub(r"\((?:[^)]*)\)/?", "", m.group(1) or "").strip("/")
                    route_file["/" + seg if seg else "/"] = rel

        fixed = 0

        # `covers` is a traceability edge, not new product design. When a
        # model planned the capability and a real workflow but forgot that
        # edge, attach it to the journey with the same actor/files/route. If
        # the journey does not already describe the operation, add one
        # concrete business step; never claim coverage without a walk.
        workflows = [w for w in (plan or {}).get("workflows") or []
                     if isinstance(w, dict)]
        cap_by_id = {str(c.get("id") or "").strip().upper(): c for c in caps
                     if c.get("id")}

        def cap_files(cap: dict) -> set:
            out = set()
            for item in cap.get("files") or []:
                rel = item.get("path") if isinstance(item, dict) else item
                if str(rel or "").strip():
                    out.add(str(rel).strip())
            return out

        def route_for_file(rel: str) -> str:
            m = re.match(r"^app/(.*?/)?page\.(?:jsx|js)$", str(rel or ""))
            if not m:
                return ""
            seg = re.sub(r"\((?:[^)]*)\)/?", "", m.group(1) or "").strip("/")
            return "/" + seg if seg else "/"

        word_stop = {"about", "after", "before", "could", "every", "from",
                     "their", "there", "these", "thing", "through", "using",
                     "visible", "where", "which", "while", "with", "user",
                     "users", "shows", "showing"}

        def useful_words(value) -> set:
            return {x for x in re.findall(r"[a-z0-9]+", str(value or "").lower())
                    if len(x) >= 4 and x not in word_stop}

        def workflow_files(workflow: dict) -> set:
            out = set()
            for other_id in workflow.get("covers") or []:
                out |= cap_files(cap_by_id.get(str(other_id).strip().upper(), {}))
            return out

        already_covered = {
            str(cid).strip().upper()
            for w in workflows for cid in (w.get("covers") or [])
        }
        for cap in caps:
            cid = str(cap.get("id") or "").strip().upper()
            if not cid or not cap.get("e2e", True) or cid in already_covered:
                continue
            files = cap_files(cap)
            routes = {route_for_file(rel) for rel in files}
            routes.discard("")
            who = str(cap.get("who") or "").strip().lower()
            proof_text = (str(cap.get("requirement") or "") + " " +
                          str(cap.get("proof") or "")).strip()
            wanted_words = useful_words(proof_text)

            same_actor = [w for w in workflows
                          if who and str(w.get("who") or "").strip().lower() == who]
            candidates = same_actor or workflows
            ranked = []
            for idx, workflow in enumerate(candidates):
                steps_text = " ".join(str(x) for x in (workflow.get("steps") or []))
                score = 0
                if who and str(workflow.get("who") or "").strip().lower() == who:
                    score += 20
                shared = files & workflow_files(workflow)
                score += 12 * min(2, len(shared))
                score += 8 * sum(1 for route in routes if route in steps_text)
                score += min(10, len(wanted_words & useful_words(
                    str(workflow.get("name") or "") + " " + steps_text)))
                ranked.append((score, -idx, workflow, steps_text))

            best = max(ranked, default=None, key=lambda item: (item[0], item[1]))
            workflow = best[2] if best else None
            route = sorted(routes, key=lambda x: ("[" in x, len(x), x))[0] \
                if routes else "/"
            requirement = str(cap.get("requirement") or cid).strip()
            proof = str(cap.get("proof") or "the visible result confirms completion").strip()
            step = f"{route} — {requirement} — {proof}"[:900]

            # A matching route or meaningful words means an existing step
            # already performs this operation and only the covers edge was
            # omitted. Otherwise add the operation explicitly.
            performed = False
            if best:
                shared_words = wanted_words & useful_words(best[3])
                performed = (any(r in best[3] for r in routes) and
                             len(shared_words) >= 2) or len(shared_words) >= 3
            if workflow is None or (not performed and len(workflow.get("steps") or []) >= 12):
                workflow = {
                    "name": f"{cid} proof",
                    "who": str(cap.get("who") or "visitor").strip() or "visitor",
                    "covers": [],
                    "steps": [step],
                }
                workflows.append(workflow)
                plan.setdefault("workflows", []).append(workflow)
                performed = True
            elif not performed:
                workflow.setdefault("steps", []).append(step)

            covers = workflow.setdefault("covers", [])
            if cid not in {str(x).strip().upper() for x in covers}:
                covers.append(cid)
                fixed += 1
                already_covered.add(cid)
                self._log("INFO", f"   🧩 {cid} was not attached to a browser "
                                  f"journey — added to \"{str(workflow.get('name') or 'workflow')[:60]}\"")

        for c in caps:
            cid = str(c.get("id") or "").upper()
            files = [str(x) for x in (c.get("files") or []) if str(x).strip()]

            if not files and cid:
                # Borrow the routes of every workflow that claims to cover it.
                found = []
                for w in (plan or {}).get("workflows") or []:
                    if not isinstance(w, dict):
                        continue
                    if cid not in {str(x).strip().upper() for x in (w.get("covers") or [])}:
                        continue
                    for step in w.get("steps") or []:
                        head = str(step).split("—")[0].split("–")[0].strip()
                        head = head.split("?", 1)[0].rstrip("/") or "/"
                        if head in route_file and route_file[head] not in found:
                            found.append(route_file[head])
                if not found:
                    requirement = str(c.get("requirement") or cid)
                    candidates = self._requirement_file_candidates(plan, requirement)
                    found = (self._enrich_requirement_files(candidates, requirement)
                             if candidates else
                             self._add_requirement_phase(plan, requirement))
                if found:
                    c["files"] = found[:6]
                    planned.update(found)
                    fixed += 1
                    self._log("INFO", f"   🧩 {cid} had no files — pinned to the "
                                      f"real task file(s) that implement it: "
                                      f"{', '.join(found[:3])}")
                continue

            missing = [f for f in files if f not in planned]
            invalid = [path for path in missing if not re.match(
                r"^(?:app|components|lib)/[\w./\[\]-]+\.(?:jsx|js)$", path)]
            if invalid:
                requirement = str(c.get("requirement") or cid)
                candidates = self._requirement_file_candidates(plan, requirement)
                replacements = (self._enrich_requirement_files(candidates, requirement)
                                if candidates else
                                self._add_requirement_phase(plan, requirement))
                kept = [path for path in files if path not in invalid]
                c["files"] = list(dict.fromkeys(kept + replacements))[:10]
                planned.update(replacements)
                files = list(c["files"])
                missing = [f for f in files if f not in planned]
                fixed += 1
                self._log("INFO", f"   🧩 {cid} named non-buildable file(s) "
                                  f"{', '.join(invalid[:3])} — replaced them "
                                  "with planned application files")
            for path in missing:
                if not re.match(r"^(?:app|components|lib)/[\w./\[\]-]+\.(?:jsx|js)$", path):
                    continue                      # Not a file this stack builds
                task = home_for(path)
                task.setdefault("files", []).append({
                    "path": path,
                    "why": f"required by {cid or 'a capability'}: "
                           f"{str(c.get('requirement') or '')[:120]}",
                })
                planned.add(path)
                fixed += 1
                self._log("INFO", f"   🧩 {path} is needed by {cid} but no task "
                                  f"built it — added to "
                                  f"\"{str(task.get('title') or 'the last task')[:40]}\"")
        return fixed
    def _stream_capability_replan(self, messages: list, on_delta,
                                  temperature: float) -> None:
        """Run one bounded planner-only recovery turn with thinking disabled."""
        self._stream(messages, on_delta, temperature=temperature,
                     model=self.planner_model, timeout=self.REPLAN_TIMEOUT,
                     max_output_tokens=self._planning_output_limit(),
                     reasoning=False)

    def make_plan(self, user_prompt: str, requirement_source: str = "") -> bool:
        self._log("INFO", "🧭 Planning — writing plan.md")
        self._fire("on_phase", {"phase": 0, "title": "Planning",
                                "status": "active"})
        self._fire("on_file_start", "plan.md")

        # The requirement ids that actually exist, taken from the brief.
        requirement_source = str(requirement_source or user_prompt or "")
        self.user_prompt = str(user_prompt or "")
        self._vocab_cache = None
        self._app_noun_cache = None
        self._known_fr = set(re.findall(r"\bFR-\d+\b", requirement_source))
        self._fr_text = self._requirement_text_map(requirement_source)
        self._source_core_features = self._core_features(requirement_source)
        self._automatic_plan_repairs = []
        source_reqs = [] if self._known_fr else self._source_requirements(requirement_source)
        source_hint = ""
        if source_reqs:
            source_hint = (
                "\n\nAgentForge extracted this ORIGINAL ACTION CHECKLIST verbatim-ish "
                "from the idea. It is independent of your Core Features list. "
                "Every line must survive into a capability; do not merge away a verb:\n"
                + "\n".join(f"- {x}" for x in source_reqs)
            )

        # Keep provenance visible to the planner too.
        operational_context = ""
        if user_prompt != requirement_source:
            if str(user_prompt).startswith(requirement_source):
                operational_context = str(user_prompt)[len(requirement_source):].strip()
            else:
                operational_context = str(user_prompt).strip()

        planner_user = (
            "AUTHORITATIVE PRODUCT REQUIREMENTS — these and only these define "
            "what the app must do:\n\n" + requirement_source + source_hint
        )
        if operational_context:
            planner_user += (
                "\n\nAGENTFORGE BUILD CONTEXT — implementation constraints/resources only. "
                "Use these while planning, but DO NOT turn them into Core Features, "
                "source requirements, user capabilities, or workflows:\n\n"
                + operational_context
            )
        if self._known_fr:
            ordered_fr = sorted(self._known_fr, key=lambda x: int(x.split('-')[1]))
            planner_user += (
                "\n\nMANDATORY COVERAGE LEDGER — copy every id below into exactly "
                "the task(s) that implement it. Before emitting the plan, count "
                "these ids against task `covers`, then count every e2e capability "
                "against workflow `covers` in the SAME self-check. Do not wait for "
                "AgentForge to ask for a replan:\n" + ", ".join(ordered_fr))
        planner_user += (
            "\n\nFIRST-ROUND PLAN PREFLIGHT — do this silently before emitting JSON: "
            "(1) every workflow control is owned by a page action and, when "
            "delegated, by the actual client component action with the same testid; "
            "(2) lib/seed.js lists every relationship read/write and an explicit "
            "parent→child seed order in invariants; (3) every task/file is ordered "
            "producer-before-consumer; (4) every contract has both ends planned; "
            "(5) task 1 proves a fresh DB can seed and render one real page without "
            "a runtime exception. Fix the plan now rather than relying on a replan."
            "\n\nWrite the plan now."
        )

        messages = [
            {"role": "system", "content": self._planner_sys()},
            {"role": "user", "content": planner_user},
        ]

        buf = []

        def on_delta(t):
            buf.append(t)
            self._fire("on_file_token", "plan.md", t)

        try:
            self._stream(messages, on_delta, temperature=0.7,
                         model=self.planner_model, reasoning=False,
                         timeout=self.PLANNER_TIMEOUT,
                         max_output_tokens=self._planning_output_limit())
        except Exception as e:
            self._log("ERROR", f"   ❌ Planner failed: {e}")
            return False

        raw = "".join(buf)
        self.plan = self._extract_plan_json(raw)
        if not self.plan.get("phases"):
            self._log("WARN", "   ⚠ No usable JSON plan — using a default phase map")
            self.plan = self._fallback_plan(user_prompt)

        short = self._roles_without_areas(self.plan)
        if short:
            roles, areas = short
            self._log("WARN", f"   ⚠ the plan gives {len(roles)} role(s) only "
                              f"{len(areas)} area(s) — asking for one per role")
            messages = messages[:2] + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content":
                    f"This plan has {len(roles)} roles — "
                    f"{', '.join(sorted(roles))} — and only "
                    f"{len(areas)} signed-in area"
                    f"{'' if len(areas) == 1 else 's'}"
                    + (f" ({', '.join(sorted(areas))})" if areas else "")
                    + ". Every role needs its own route prefix, and each screen "
                      "the request describes for a role belongs at its own route "
                      "under that prefix — not as a tab inside a shared page. "
                      "Rewrite the plan with a section per role, keeping "
                      "everything else you already decided. Emit the whole plan "
                      "again, including the JSON block."},
            ]
            buf2 = []
            try:
                self._stream(messages, buf2.append, temperature=0.7,
                             model=self.planner_model,
                             timeout=self.REPLAN_TIMEOUT,
                             max_output_tokens=self._planning_output_limit(),
                             reasoning=False)
                again = self._extract_plan_json("".join(buf2))

                if again.get("phases") and not self._roles_without_areas(again):
                    self.plan, raw = again, "".join(buf2)
                    self._log("INFO", "   ✅ replanned with a section per role")
                else:
                    self._log("WARN", "   ⚠ the replan did not add them — "
                                      "keeping the first plan")
            except Exception as e:
                self._log("WARN", f"   ⚠ replan failed: {e}")

        # The second half-app: a specification the plan quietly shrank.
        missing = self._uncovered(self.plan)
        if missing:
            repaired = self._repair_requirement_coverage(self.plan, missing)
            if repaired:
                missing = self._uncovered(self.plan)
                self._log("INFO", f"   ✅ placed {repaired} omitted requirement "
                                  f"ledger id(s) from existing capability/file evidence")
        if missing:
            self._log("WARN", f"   ⚠ the plan leaves {len(missing)} "
                              f"requirement(s) with no task — "
                              f"{', '.join(missing[:6])}"
                              f"{'…' if len(missing) > 6 else ''} — asking "
                              f"for them to be placed")
            messages = messages[:2] + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content":
                    f"These requirements from the specification appear in no "
                    f"task's `covers`: {', '.join(missing)}. Each one is a "
                    f"feature the finished app will not have. Put every one "
                    f"of them on the task that builds it — add a task if none "
                    f"fits, or add the files that were missing — and keep "
                    f"everything else you already decided. If one genuinely "
                    f"cannot be built, say so under ## Overview and still list "
                    f"it in `covers` on the task that comes closest. ALSO do the "
                    f"capability/workflow completeness check in this SAME rewrite: "
                    f"every browser-visible capability has exact files and e2e=true, "
                    f"and every e2e capability is covered by a workflow that performs "
                    f"its proof. This is one repair pass, not two. Emit the whole plan "
                    f"again, including the JSON block."},
            ]
            buf3 = []
            try:
                self._stream(messages, buf3.append, temperature=0.7,
                             model=self.planner_model,
                             timeout=self.REPLAN_TIMEOUT,
                             max_output_tokens=self._planning_output_limit(),
                             reasoning=False)
                again = self._extract_plan_json("".join(buf3))
                left = self._uncovered(again) if again.get("phases") else missing
                if again.get("phases") and len(left) < len(missing):
                    self.plan, raw = again, "".join(buf3)
                    self._log("INFO", f"   ✅ replanned — "
                                      f"{len(missing) - len(left)} of "
                                      f"{len(missing)} placed"
                                      + (f", {len(left)} still loose: "
                                         f"{', '.join(left[:4])}" if left else ""))
                else:
                    self._log("WARN", "   ⚠ the replan did not place them — "
                                      "keeping the first plan")
            except Exception as e:
                self._log("WARN", f"   ⚠ replan failed: {e}")

        # The third half-app, and the one people actually see.
        stub = self._unbuilt_routes(self.plan, raw)
        if stub:
            self._log("WARN", f"   ⚠ the plan promises {len(stub)} route(s) "
                              f"no file serves — {', '.join(stub[:6])}"
                              f"{'…' if len(stub) > 6 else ''} — asking for "
                              f"them to be written or dropped")
            messages = messages[:2] + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content":
                    f"These paths appear in your plan — in the Routes table, "
                    f"the Page Flow, or a link — but no task lists a file that "
                    f"serves them: {', '.join(stub)}.\n\n"
                    f"Every one is a 404 in the finished app. For each, do ONE "
                    f"of two things and do it everywhere:\n"
                    f"  • it belongs in the app — add its file to the task that "
                    f"builds that part (`app/<path>/page.jsx`, or "
                    f"`app/api/<name>/route.js` for an API path), or\n"
                    f"  • it does not — remove it from the Routes table, from "
                    f"the Page Flow, and from every link that points at it, so "
                    f"nothing links to a page that will not exist.\n\n"
                    f"A path mentioned once more than it is built is the whole "
                    f"defect, so check the three places agree. Keep everything "
                    f"else you already decided and emit the whole plan again, "
                    f"including the JSON block."},
            ]
            buf4 = []
            try:
                self._stream(messages, buf4.append, temperature=0.6,
                             model=self.planner_model,
                             timeout=self.REPLAN_TIMEOUT,
                             max_output_tokens=self._planning_output_limit(),
                             reasoning=False)
                fixed = self._extract_plan_json("".join(buf4))
                if fixed.get("phases"):
                    left = self._unbuilt_routes(fixed, "".join(buf4))
                    if len(left) < len(stub):
                        self.plan, raw = fixed, "".join(buf4)
                        self._log("INFO", f"   ✅ replanned — "
                                          f"{len(stub) - len(left)} of "
                                          f"{len(stub)} route(s) settled"
                                          + (f", {len(left)} still loose"
                                             if left else ""))
                    else:
                        self._log("WARN", "   ⚠ the replan did not settle them "
                                          "— keeping the first plan")
            except Exception as e:
                self._log("WARN", f"   ⚠ route replan failed: {e}")

        # Free-form prompts have no FR ids.
        self.plan["source_requirements"] = source_reqs

        repaired_map = self._repair_capability_map(self.plan)
        if repaired_map:
            self._log("INFO", f"   ✅ repaired {repaired_map} mechanical "
                              "capability/workflow edge(s) without rewriting the plan")
        raw = self._converge_capability_map(messages, raw, source_reqs)

        final_cap_gaps = self._capability_gaps(self.plan, raw)
        if any(final_cap_gaps):
            # One final controller pass after the six-round convergence budget.
            self._repair_missing_capabilities(self.plan, final_cap_gaps[0])
            self._repair_capability_map(self.plan)
            self._repair_inert_plan(self.plan)
            final_cap_gaps = self._capability_gaps(self.plan, raw)

        # `missing_features` holds two different kinds of thing
        source_only = [g for g in final_cap_gaps[0] if str(g).startswith("SOURCE:")]
        hard_missing = [g for g in final_cap_gaps[0] if not str(g).startswith("SOURCE:")]
        if source_only and not (hard_missing or final_cap_gaps[1] or final_cap_gaps[2]):
            self._log("WARN", "   ⚠ the plan does not obviously cover "
                              f"{len(source_only)} phrase(s) read out of the "
                              "brief — building anyway, the later gates check "
                              "what actually shipped")
            for g in source_only[:4]:
                self._log("WARN", f"      • {str(g)[:160]}")
            final_cap_gaps = ([], final_cap_gaps[1], final_cap_gaps[2])

        if any(final_cap_gaps):
            pieces = []
            if final_cap_gaps[0]: pieces.append("unmapped source/Core requirements: " + "; ".join(final_cap_gaps[0][:5]))
            if final_cap_gaps[1]: pieces.append("unwalked capabilities: " + ", ".join(final_cap_gaps[1][:8]))
            if final_cap_gaps[2]: pieces.append("capabilities without planned files / inert plan work: " + ", ".join(final_cap_gaps[2][:8]))
            self._log("ERROR", "   ❌ Planner recovery could not produce a "
                               "buildable capability map — " + " | ".join(pieces))
            for d in (getattr(self, "_last_gap_details", None) or [])[:6]:
                self._log("ERROR", f"      • {d}")
            self._log("ERROR", f"      The planner and controller exhausted "
                               f"{self.CAPABILITY_REPLAN_ROUNDS} bounded repair "
                               "rounds; the exact remaining evidence is above.")
            self._fire("on_phase", {"phase": 0, "title": "Planning", "status": "error",
                                    "reason": "capability map incomplete"})
            return False

        self.plan_md = re.sub(r"```json.*?```", "", raw, flags=re.S).strip()
        repairs = list(getattr(self, "_automatic_plan_repairs", None) or [])
        if repairs:
            rows = ["## Automatic completeness repairs", ""]
            for item in repairs:
                rows.append(f"- **{item['capability']}** — {item['requirement']}")
                rows.append("  - Files: " + ", ".join(f"`{p}`" for p in item["files"]))
            self.plan_md = (self.plan_md + "\n\n" + "\n".join(rows)).strip()
        self._fire("on_file_end", "plan.md", self.plan_md)
        self.write_file("plan.md", self.plan_md)

        # The plan's companion: what to build, and how it should look.
        self.design_md = self._design_md()
        self.write_own("design.md", self.design_md)
        self._save_plan_json()

        n = len(self.plan["phases"])
        self._log("INFO", f"   ✅ Plan ready — {n} tasks, "
                          f"{sum(len(p.get('files', [])) for p in self.plan['phases'])} files")

        self.start_conversation(user_prompt)

        self.save_convo()

        self._fire("on_phase", {"phase": 0, "title": "Planning",
                                "status": "done", "plan": self.plan})
        return True
