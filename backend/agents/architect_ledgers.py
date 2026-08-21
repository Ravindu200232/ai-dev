"""Contract/capability/data/flow/design ledgers and task context."""
from .architect_common import *


class ArchitectLedgerMixin:
    def _outstanding_by_task(self) -> str:
        """What is left, under the task each file belongs to."""
        blocks = []
        now, _t, _left = self._current_task()
        for i, t in enumerate(self.plan.get("phases", []), start=1):
            left = [f for f in t.get("files", []) if not self._on_disk(f["path"])]
            if not left:
                continue
            head = f"### Task {i} — {t.get('title', f'Task {i}')}"
            if i == now:
                head += "        ← THIS ONE, now"
            if t.get("goal"):
                head += f"\nGoal: {t['goal']}"
            if t.get("done_when"):
                head += f"\nDone when: {t['done_when']}"
            if t.get("covers"):
                head += f"\nCovers: {', '.join(t['covers'])}"
            block = head + "\n" + self._file_list_block(left)
            caps = self._capability_ledger(left)
            if caps:
                block += "\nCapabilities touching what is left:\n" + caps
            handoffs = self._contract_ledger(left)
            if handoffs:
                block += "\nCross-file contracts touching what is left:\n" + handoffs
            blocks.append(block)
        return "\n\n".join(blocks)

    @staticmethod
    def _url_of_planned(path: str) -> str:
        """The URL a planned page file serves, route groups removed."""
        p = (path or "").replace("\\", "/")
        if not p.startswith("app/"):
            return ""
        parts = p[len("app/"):].split("/")
        if not parts or not parts[-1].startswith("page."):
            return ""
        segs = [x for x in parts[:-1]
                if not (x.startswith("(") and x.endswith(")"))]
        return "/" + "/".join(segs) if segs else "/"

    @staticmethod
    def _runtime_url_of_planned(path: str) -> str:
        """Runtime URL for a planned page OR route handler."""
        p = (path or "").replace("\\", "/")
        if not p.startswith("app/"):
            return ""
        parts = p[len("app/"):].split("/")
        if not parts or not re.match(r"^(?:page|route)\.(?:jsx?|tsx?)$", parts[-1]):
            return ""
        segs = [x for x in parts[:-1]
                if not (x.startswith("(") and x.endswith(")"))]
        return "/" + "/".join(segs) if segs else "/"

    def _planned_url_map(self) -> dict:
        """`runtime URL -> planned file`, first declaration wins."""
        out = {}
        for f in self._planned_files():
            path = f.get("path", "")
            url = self._runtime_url_of_planned(path)
            if url and url not in out:
                out[url] = path
        return out

    def _contract_ledger(self, wanted: list = None) -> str:
        """The exact hand-offs touching the files in this turn."""
        contracts = (self.plan or {}).get("contracts") or []
        if not contracts:
            return ""
        paths = {x if isinstance(x, str) else (x or {}).get("path", "")
                 for x in (wanted or [])}
        paths.discard("")
        url_map = self._planned_url_map()
        rows = []
        for c in contracts:
            src = c.get("from", "")
            target = c.get("target", "")
            dst = url_map.get(target, "")
            if paths and src not in paths and dst not in paths:
                continue
            head = f"  • {c.get('name') or c.get('kind')}: {src} —{c.get('trigger') or 'action'}→ {target}"
            if c.get("kind") == "api":
                head += f" [{c.get('method') or 'API'}]"
            rows.append(head)
            if target.startswith("/") and "[" not in str(target):
                rows.append(f"      caller must contain the literal "
                            f"'{target}' — write fetch('{target}'), not a "
                            f"variable that resolves to it")
            req = c.get("request") or []
            res = c.get("response") or []
            if req:
                rows.append("      request fields: " + ", ".join(req))
            if res:
                rows.append("      response fields: " + ", ".join(res))
            if c.get("effect"):
                rows.append("      success must: " + c["effect"])
        return "\n".join(rows[:36])

    def _related_context_files(self, wanted: list) -> set:
        """Existing files that are semantic neighbours of an unwritten file."""
        wanted_paths = {x if isinstance(x, str) else (x or {}).get("path", "")
                        for x in (wanted or [])}
        wanted_paths.discard("")
        if not wanted_paths:
            return set()
        url_map = self._planned_url_map()
        related = set()
        for c in (self.plan or {}).get("contracts") or []:
            src = c.get("from", "")
            dst = url_map.get(c.get("target", ""), "")
            if src in wanted_paths and dst and self._on_disk(dst):
                related.add(dst)
            if dst in wanted_paths and src and self._on_disk(src):
                related.add(src)

        planned = self._planned_files()
        wanted_urls = {self._runtime_url_of_planned(p) for p in wanted_paths}
        wanted_urls.discard("")
        for f in planned:
            path = f.get("path", "")
            actions = " ".join(f.get("actions") or [])
            own_url = self._runtime_url_of_planned(path)
            if path in wanted_paths:
                for url, other in url_map.items():
                    if url != "/" and url in actions and self._on_disk(other):
                        related.add(other)
            elif self._on_disk(path) and own_url:
                if any(url != "/" and url in actions for url in wanted_urls):
                    related.add(path)
        return related

    _ARROW_TEX = re.compile(r"\s*(?:\u2192|-->|->|\$*\\[a-zA-Z]*(?:to|arrow)\s*\$*)\s*")

    def _section_of_plan(self, heading: str, limit: int = 22) -> str:
        """One `## …` section of plan.md, back out of the plan text."""
        md = self.plan_md or ""
        i = md.find(heading)
        if i < 0:
            return ""
        rest = md[i + len(heading):]
        j = rest.find("\n## ")
        body = (rest[:j] if j > 0 else rest).strip("\n")
        body = self._ARROW_TEX.sub(" → ", body)
        lines = [ln.rstrip() for ln in body.splitlines() if ln.strip()]
        return "\n".join(lines[:limit])

    def _capability_ledger(self, wanted=None) -> str:
        """Compact promises touching the current files, re-sent every turn."""
        caps = (self.plan or {}).get("capabilities") or []
        wanted = {str(x).replace("\\", "/") for x in (wanted or [])}
        lines = []
        for c in caps:
            files = [str(x).replace("\\", "/") for x in c.get("files") or []]
            if wanted and files and not (wanted & set(files)):
                continue
            tag = c.get("id") or "CAP"
            who = f" [{c.get('who')}]" if c.get("who") else ""
            proof = f"; proof: {c.get('proof')}" if c.get("proof") else ""
            lines.append(f"{tag}{who}: {c.get('requirement','')}{proof}")
        return "\n".join(lines[:30])

    def _data_ledger(self) -> str:
        """The main Mongo collection/field vocabulary from plan.md."""
        return self._section_of_plan("## Data Model", limit=28)

    def _flow_ledger(self) -> str:
        """The journeys, and which page leads to which."""
        return self._section_of_plan("## Page Flow")

    _DESIGN_LINE = re.compile(r"^\s*[-*]\s*(.+)$")

    def _design_ledger(self) -> str:
        """The design decisions, in the words the plan used."""
        md = self.plan_md or ""
        i = md.find("## Design System")
        if i < 0:
            return ""
        rest = md[i + len("## Design System"):]
        j = rest.find("\n## ")
        body = rest[:j] if j > 0 else rest
        bits = []
        for line in body.splitlines():
            m = self._DESIGN_LINE.match(line)
            if m:
                bits.append(" ".join(m.group(1).split())[:110])
            if len(bits) >= 8:
                break
        return "\n".join("  " + b for b in bits)

    def _house_ledger(self) -> str:
        """What every remaining file has to agree with, rebuilt each turn."""
        parts = []

        source = (self.plan or {}).get("source_requirements") or []
        if source:
            parts.append("Original user jobs — these came from the request, not from the planner, "
                         "so none may disappear during later tasks:\n" +
                         "\n".join(f"- {x}" for x in source[:24]))

        caps = self._capability_ledger()
        if caps:
            parts.append("The capabilities this app must actually prove — a screen or button "
                         "existing is not enough:\n" + caps)

        data = self._data_ledger()
        if data:
            parts.append("Canonical data model — collection and field names are "
                         "an API contract, not synonyms. Reuse these exact names "
                         "in seed, queries, forms and JSON:\n" + data)

        flow = self._flow_ledger()
        if flow:
            parts.append("How this app hangs together — the journeys, and "
                         "which page leads to which. The page you are writing "
                         "is not finished until whatever leads to it says so:\n"
                         + flow)

        design = self._design_ledger()
        if design:
            parts.append("The design this app is already in — match it, do not "
                         "restate it differently:\n" + design)

        plan = self.plan or {}
        roles = [str(a.get("role") or "").strip()
                 for a in (plan.get("demo_accounts") or [])]
        roles = [r for r in roles if r]
        if roles:
            signup = str(plan.get("signup_role") or "").strip()
            named = ", ".join(
                f"{r} (what signing up creates)" if r == signup else r
                for r in dict.fromkeys(roles))
            parts.append(f"The roles: {named}. A page decides who it is for "
                         f"from this list and no other.")

        urls, left = [], set()
        for f in self._planned_files():
            u = self._url_of_planned(f.get("path", ""))
            if not u:
                continue
            urls.append(u)
            if not self._on_disk(f["path"]):
                left.add(u)
        urls = list(dict.fromkeys(urls))
        if urls:
            shown = ", ".join(u + ("*" if u in left else "") for u in urls[:40])
            more = f" … and {len(urls) - 40} more" if len(urls) > 40 else ""
            parts.append(f"Every URL this app serves: {shown}{more}\n"
                         f"  Link only to these. A href to anything else is a "
                         f"dead link, and one marked * is not written yet — "
                         f"link to it anyway, it is coming.")

        if not parts:
            return ""
        return "\n\n".join(parts)

    THIN_LINES = 40

    def _task_note(self, index: int, task: dict) -> str:
        """What the task that just finished actually left behind."""
        files = [f for f in (task.get("files") or [])
                 if self._on_disk(f.get("path", ""))]
        if not files:
            return ""

        thin, unlinked = [], []
        for f in files:
            rel = f["path"]
            body = self.files.get(rel) or ""
            lines = body.count("\n") + 1
            want = len(f.get("sections") or [])
            if want >= 3 and lines < self.THIN_LINES:
                thin.append(f"{rel} is {lines} lines for {want} sections")
            for act in (f.get("actions") or [])[:6]:
                for route in self._ROUTE_TOKEN.findall(str(act)):
                    if route not in body and len(unlinked) < 4:
                        unlinked.append(f"{rel} never links to {route}, which "
                                        f"its plan says it opens")

        problems = []
        for finding in (self.client_server_mix() or [])[:3]:
            problems.append(str(finding)[:150])
        for finding in (self.component_props_to_client() or [])[:2]:
            problems.append(str(finding)[:150])
        for finding in (self.bson_props_to_client() or [])[:2]:
            problems.append(str(finding)[:150])
        # Scoped to the files this task wrote.
        mine = set()
        for f in files:
            for name in self.imported_packages(self.files.get(f["path"]) or ""):
                mine.add(name)
        gone = [p for p in (self.unresolved_packages() or [])
                if p in mine and p not in self.BANNED_DEPS][:4]
        if gone:
            problems.append("imported but not installed: " + ", ".join(gone))

        # A task is not complete merely because its files exist.
        task_paths = {f.get("path", "") for f in files}
        url_map = self._planned_url_map()
        for c in (self.plan or {}).get("contracts") or []:
            src = c.get("from", "")
            dst = url_map.get(c.get("target", ""), "")
            if src not in task_paths and dst not in task_paths:
                continue
            target = c.get("target", "")
            src_body = self.files.get(src, "")
            if (src_body and "[" not in target and target not in src_body
                    and len(problems) < 5):
                problems.append(f"contract {c.get('name','')}: {src} never names "
                                f"{target} for {c.get('trigger') or 'its action'}")
            if c.get("kind") == "api" and dst and self._on_disk(dst):
                route_body = self.files.get(dst, "")
                method = c.get("method") or ""
                if method and route_body and not re.search(
                        rf"export\s+(?:async\s+)?(?:function|const)\s+{re.escape(method)}\b",
                        route_body) and len(problems) < 5:
                    problems.append(f"contract {c.get('name','')}: {dst} does not "
                                    f"export {method} for {target}")

        if not (thin or unlinked or problems):
            return (f"Task {index} — {task.get('title', '')} is written: "
                    f"{len(files)} file(s), nothing outstanding in them.")

        out = [f"Task {index} — {task.get('title', '')} is written "
               f"({len(files)} file(s)), and a look at it turned up:"]
        for line in (thin + unlinked + problems)[:6]:
            out.append(f"  • {line}")
        out.append("Put these right as part of this turn — they are one turn "
                   "old and everything after builds on them.")
        return "\n".join(out)
