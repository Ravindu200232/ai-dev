"""Unbuilt-promise analysis, scoped repair."""
from .analyzer_common import *


class AnalyzerRepairFindingsMixin:
    # Structured output parser used by `unbuilt_promises`.
    PROMISE_RE = re.compile(
        r"PROMISE:\s*(.+?)\s*\n\s*FILE:\s*([^\n]+?)\s*\n\s*MISSING:\s*(.+?)(?=\n\s*PROMISE:|\Z)",
        re.S)

    @staticmethod
    def _norm(s: str) -> str:
        """Whitespace- and punctuation-insensitive, for quote matching."""
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    def unbuilt_promises(self, max_reads: int = 10) -> list:
        """Behaviour the plan promised in prose that no file actually implements."""
        plan = self.plan_text()
        if not plan.strip():
            return []
        files = self.code_files()
        if not files:
            return []

        # The prose plan tells us the user journey.
        machine_lines = []
        plan_obj = getattr(self.arch, "plan", None) or {}
        for c in plan_obj.get("capabilities") or []:
            if not isinstance(c, dict):
                continue
            machine_lines.append(
                f"CAPABILITY {c.get('id') or 'CAP'} [{c.get('who') or ''}]: "
                f"{c.get('requirement') or ''}; proof={c.get('proof') or ''}; "
                f"files={','.join(c.get('files') or [])}; e2e={bool(c.get('e2e', True))}")
        for ph in plan_obj.get("phases") or []:
            for f in ph.get("files") or []:
                if not isinstance(f, dict):
                    continue
                for a in (f.get("actions") or [])[:10]:
                    machine_lines.append(f"FILE_ACTION {f.get('path')}: {a}")
                if f.get("reads"):
                    machine_lines.append(f"FILE_READS {f.get('path')}: {', '.join(f.get('reads') or [])}")
                if f.get("writes"):
                    machine_lines.append(f"FILE_WRITES {f.get('path')}: {', '.join(f.get('writes') or [])}")
        for w in plan_obj.get("workflows") or []:
            name = str(w.get("name") or "workflow")
            who = str(w.get("who") or "")
            for step in (w.get("steps") or [])[:12]:
                machine_lines.append(f"WORKFLOW {name} [{who}]: {step}")
        for c in (getattr(self.arch, "plan", None) or {}).get("contracts") or []:
            method = (f" {c.get('method')}" if c.get("kind") == "api" and c.get("method") else "")
            req = ", ".join(c.get("request") or [])
            res = ", ".join(c.get("response") or [])
            machine_lines.append(
                f"CONTRACT {c.get('name') or c.get('kind')}: {c.get('from')} — "
                f"{c.get('trigger') or 'action'} →{method} {c.get('target')}; "
                f"request=[{req}]; response=[{res}]; success={c.get('effect') or ''}")
        machine = "\n".join(machine_lines)[:12000]

        orphans = self.orphan_components()
        hint = (f"\n\n## Components that exist but nothing renders\n"
                f"{', '.join(orphans[:12])}\n"
                f"Each one is either a dropped feature or harmless scaffolding "
                f"— the plan decides which.\n" if orphans else "")

        messages = [
            {"role": "system", "content":
                "You audit a finished Next.js App Router app against the approved "
                "plan AND its structured workflow/contracts. You are looking for "
                "behaviour that is present on paper but not connected end-to-end "
                "in the code.\n\n"
                "Not style. Not missing files — another check owns those. Not "
                "polish. Follow real journeys: control → handler/fetch → route "
                "method → Mongo read/write → success UI/navigation → destination. "
                "A screen existing is not enough if its button does nothing, its "
                "API uses a different field name, the mutation is not persisted, "
                "or success never reaches the next screen. CAPABILITY lines are "
                "the completeness ledger: check every one whose files are named, "
                "including filters/search/availability that may live entirely on "
                "one page. Decorative inputs plus an inert button do NOT implement "
                "a capability. FILE_READS/FILE_WRITES are promises too.\n\n"
                "Read files before judging:\n"
                "  <read_file path=\"app/admin/page.js\"/>\n"
                "Up to 4 per reply. Read the file that would hold the feature "
                "before claiming it is absent — a promise implemented somewhere "
                "you did not look is the mistake that makes this audit useless.\n\n"
                "When done, output one block per finding, nothing else:\n"
                "  PROMISE: <an exact sentence/bullet from the plan OR one exact "
                "CAPABILITY/FILE_ACTION/FILE_READS/FILE_WRITES/WORKFLOW/CONTRACT "
                "line from the structured memory, copied "
                "character for character>\n"
                "  FILE: <the one project-relative file that should implement it>\n"
                "  MISSING: <what specifically is not there, in one sentence>\n\n"
                "The PROMISE must be copied verbatim from either evidence block — "
                "a quote not present there is discarded. For a workflow, inspect "
                "every hop before saying it is complete. If everything promised "
                "is implemented and connected, output exactly: NONE"},
            {"role": "user", "content":
                f"## The prose plan\n{plan[:14000]}\n\n"
                + (f"## Structured workflow / cross-file contract memory\n"
                   f"{machine}\n\n" if machine else "")
                + f"## Files on disk\n{self.inventory()[:6000]}{hint}\n\n"
                "Read the caller, API/data path and destination needed to prove "
                "each journey, then report."},
        ]

        reads, used, out = 0, 0, ""
        budget = self._budget_chars()
        while True:
            chunks = []
            try:
                self.arch._stream(messages, lambda t: chunks.append(t),
                                  temperature=0.2)
            except Exception as e:
                self._log("WARN", f"   promise audit failed: {e}")
                return []
            out = "".join(chunks)
            messages.append({"role": "assistant", "content": out})
            used += len(out)

            wanted = [p for p in self.READ_RE.findall(out)][:4]
            if not (wanted and reads < max_reads and used < budget):
                break
            served = [f"--- {p} ---\n{self._read_for_model(p)}" for p in wanted]
            reads += len(wanted)
            used += sum(len(s) for s in served)
            messages.append({"role": "user",
                             "content": "\n\n".join(served) + "\n\nContinue."})

        if "NONE" in out and not self.PROMISE_RE.search(out):
            return []

        evidence_norm = self._norm(plan + "\n" + machine)
        findings = []
        for promise, path, missing in self.PROMISE_RE.findall(out):
            promise, path = promise.strip().strip("`\"'"), path.strip().strip("`")

            if len(self._norm(promise)) < 25:
                continue
            if self._norm(promise) not in evidence_norm:
                self._log("INFO", f"   ↯ dropped an invented promise: "
                                  f"{promise[:60]}")
                continue
            if path not in files:
                self._log("INFO", f"   ↯ dropped a promise naming no real "
                                  f"file: {path}")
                continue
            findings.append(Finding(
                "major", "UNBUILT_PROMISE",
                f"the approved flow promises “{promise[:180]}” but "
                f"{missing.strip()[:200]}",
                path=path,
                fix=f"implement it in {path}, changing nothing else",
                extra=[path]))
            if len(findings) >= 5:
                break
        return findings


