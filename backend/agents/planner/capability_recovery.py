"""Deterministic recovery for incomplete planner capability maps."""
import json
import re


class CapabilityRecoveryMixin:
    """Turn omitted requirement proofs into concrete builder work."""

    @staticmethod
    def _requirement_body(value: str) -> str:
        text = str(value or "").strip()
        return text[len("SOURCE:"):].strip() if text.startswith("SOURCE:") else text

    @staticmethod
    def _requirement_slug(value: str) -> str:
        label = CapabilityRecoveryMixin._requirement_body(value).split(":", 1)[0]
        words = [w for w in re.findall(r"[a-z0-9]+", label.lower())
                 if w not in {"and", "the", "for", "with", "management",
                              "system", "feature", "secure", "automatic"}]
        return "-".join(words[:5])[:54].strip("-") or "required-workflow"

    def _role_for_requirement(self, requirement: str, plan: dict = None) -> str:
        """Pick an exact seeded role; never invent or combine role names."""
        roles = list(dict.fromkeys(
            str(a.get("role") or "").strip()
            for a in (plan or self.plan or {}).get("demo_accounts") or []
            if isinstance(a, dict) and str(a.get("role") or "").strip()))
        if not roles:
            return "visitor"
        wanted = self._capability_words(requirement)
        ranked = [(len(wanted & self._capability_words(role)), -i, role)
                  for i, role in enumerate(roles)]
        score, _index, role = max(ranked)
        return role if score else roles[0]

    @staticmethod
    def _next_capability_id(plan: dict) -> str:
        used = {str(c.get("id") or "").strip().upper()
                for c in (plan or {}).get("capabilities") or []
                if isinstance(c, dict)}
        number = 1
        while f"CAP-{number:03d}" in used:
            number += 1
        return f"CAP-{number:03d}"

    def _requirement_file_candidates(self, plan: dict, requirement: str) -> list:
        """Rank already-planned files by the requirement work they describe."""
        wanted = self._capability_words(requirement)
        anchors = self._capability_words(requirement.split(":", 1)[0])
        ranked = []
        for phase_index, phase in enumerate((plan or {}).get("phases") or []):
            if not isinstance(phase, dict):
                continue
            phase_text = " ".join(str(phase.get(k) or "")
                                  for k in ("title", "goal", "done_when"))
            for file_index, spec in enumerate(phase.get("files") or []):
                if not isinstance(spec, dict) or not spec.get("path"):
                    continue
                rel = str(spec["path"])
                blob = phase_text + " " + " ".join(
                    str(v) if not isinstance(v, list) else " ".join(map(str, v))
                    for v in spec.values())
                terms = self._capability_words(blob)
                shared = wanted & terms
                anchor_shared = anchors & terms
                score = len(shared) * 4 + len(anchor_shared) * 10
                if re.match(r"^app/.*/page\.(?:jsx|js)$", rel):
                    score += 2
                if score:
                    ranked.append((score, -phase_index, -file_index, rel, spec, phase))
        ranked.sort(reverse=True, key=lambda row: row[:3])
        if not ranked:
            return []
        floor = max(4, ranked[0][0] // 3)
        return [row[3:] for row in ranked if row[0] >= floor][:5]

    def _add_requirement_phase(self, plan: dict, requirement: str) -> list:
        """Create a concrete page/component/API task when the planner omitted it."""
        body = self._requirement_body(requirement)
        label = body.split(":", 1)[0].strip() or "Required workflow"
        terms = self._capability_words(body)
        auth = bool({"identity", "authorization"} & terms)
        slug = "login" if auth else self._requirement_slug(body)
        pascal = "".join(w.capitalize() for w in slug.split("-")) or "Required"
        page_path = f"app/{slug}/page.jsx"
        component_path = ("components/LoginForm.jsx" if auth else
                          f"components/{pascal}Workspace.jsx")
        target_paths = [page_path, component_path]
        if auth:
            target_paths.append("lib/authorization.js")
        else:
            target_paths.append(f"app/api/{slug}/route.js")

        phases = plan.setdefault("phases", [])
        planned = {str(f.get("path") or "") for phase in phases
                   if isinstance(phase, dict)
                   for f in phase.get("files") or [] if isinstance(f, dict)}
        paths = [path for path in target_paths if path not in planned]
        if not paths:
            return target_paths

        clauses = [" ".join(x.split()).strip(" .") for x in
                   re.split(r"\s*;\s*|\s*,\s*|\.\s+", body)
                   if " ".join(x.split()).strip(" .")][:7]
        success = f"{label} completed"
        failure = f"Could not complete {label.lower()}"
        testid = (slug + "-primary")[:70]
        files = []
        for path in paths:
            if path.startswith("app/api/"):
                files.append({
                    "path": path, "kind": "route",
                    "purpose": f"Persist and validate the complete {body}",
                    "reads": [slug.replace("-", "_")],
                    "writes": [slug.replace("-", "_")],
                    "sections": [],
                    "actions": [f"POST validates the full request, performs {body}, and returns the durable result"],
                    "invariants": [f"never acknowledge success unless {body} is durably true"],
                })
            elif path.startswith("components/"):
                files.append({
                    "path": path, "kind": "client",
                    "purpose": f"Interactive controls for the complete {body}",
                    "reads": [], "writes": [], "sections": clauses,
                    "actions": [f"perform {body} — testid={testid}; on success show '{success}', on failure show '{failure}'"],
                    "invariants": ["every visible action works; no placeholder, disabled-for-now, or coming-soon control"],
                })
            elif path.startswith("lib/"):
                files.append({
                    "path": path, "kind": "server",
                    "purpose": f"Server-side rules that enforce {body}",
                    "reads": ["users", "sessions"], "writes": [],
                    "sections": [], "actions": [],
                    "invariants": ["deny by default and never trust a client-supplied role"],
                })
            else:
                files.append({
                    "path": path, "kind": "server",
                    "purpose": f"Complete working screen for {body}",
                    "reads": [slug.replace("-", "_")], "writes": [],
                    "sections": clauses,
                    "actions": [f"the primary control performs {body} through the real client component"],
                    "invariants": ["render real persisted state, including loading, empty, success, and error states"],
                })

        numeric = [int(p.get("id")) for p in phases if isinstance(p, dict)
                   and str(p.get("id", "")).isdigit()]
        phase = {
            "id": max(numeric, default=0) + 1,
            "title": label[:100],
            "goal": body[:500],
            "done_when": f"A real user completes {body} and sees the durable result after reload",
            "covers": [], "files": files,
        }
        insert_at = len(phases)
        if phases and re.search(r"polish|responsive|accessibility",
                                str(phases[-1].get("title") or ""), re.I):
            insert_at -= 1
        phases.insert(insert_at, phase)
        self._log("INFO", f"   🧩 created a complete {label} task because no "
                          "planned screen/file owned that requirement")
        return target_paths

    def _enrich_requirement_files(self, candidates: list, requirement: str) -> list:
        """Make selected files actually instruct the builder to perform the work."""
        body = self._requirement_body(requirement)
        label = body.split(":", 1)[0].strip() or "Required workflow"
        slug = self._requirement_slug(body)
        selected = []
        for rel, spec, _phase in candidates:
            if rel not in selected:
                selected.append(rel)
            purpose = str(spec.get("purpose") or "").strip()
            if body.lower() not in purpose.lower():
                spec["purpose"] = (purpose + ". " if purpose else "") + \
                    f"Must completely implement: {body}"
            if re.match(r"^app/.*/page\.(?:jsx|js)$", rel) or rel.startswith("components/"):
                sections = spec.setdefault("sections", [])
                if not any(body.lower() in str(x).lower() for x in sections):
                    sections.append(f"Complete {body} interface with loading, empty, success, and error states")
                actions = spec.setdefault("actions", [])
                if not any(body.lower() in str(x).lower() for x in actions):
                    actions.append(
                        f"perform {body} — testid={slug}-primary; show success toast "
                        f"'{label} completed' or error toast 'Could not complete {label.lower()}'")
        return selected[:6]

    def _repair_missing_capabilities(self, plan: dict, missing: list) -> int:
        """Turn every omitted source/Core requirement into buildable proof."""
        if not missing or not (plan or {}).get("phases"):
            return 0
        fixed = 0
        for raw_requirement in missing:
            requirement = self._requirement_body(raw_requirement)
            if not requirement:
                continue
            wanted = self._capability_words(requirement)
            anchors = self._capability_words(requirement.split(":", 1)[0])
            existing = [c for c in plan.get("capabilities") or [] if isinstance(c, dict)]
            if any((anchors & self._capability_words(
                        str(c.get("requirement") or "") + " " + str(c.get("proof") or "")))
                   or len(wanted & self._capability_words(
                        str(c.get("requirement") or "") + " " + str(c.get("proof") or "")))
                        >= min(2, len(wanted))
                   for c in existing):
                continue

            candidates = self._requirement_file_candidates(plan, requirement)
            files = self._enrich_requirement_files(candidates, requirement) \
                if candidates else self._add_requirement_phase(plan, requirement)
            if not files:
                continue
            cid = self._next_capability_id(plan)
            who = self._role_for_requirement(requirement, plan)
            proof = (f"A browser completes the full requirement and verifies its visible, "
                     f"persisted result and denied states after reload: {requirement}")[:500]
            plan.setdefault("capabilities", []).append({
                "id": cid, "who": who, "requirement": requirement[:320],
                "proof": proof, "files": files[:10], "e2e": True,
            })
            route = "/"
            for rel in files:
                match = re.match(r"^app/(.*?/)?page\.(?:jsx|js)$", rel)
                if match:
                    segment = re.sub(r"\([^)]*\)/?", "", match.group(1) or "").strip("/")
                    route = "/" + segment if segment else "/"
                    break
            plan.setdefault("workflows", []).append({
                "name": f"{requirement.split(':', 1)[0][:60]} proof",
                "who": who, "covers": [cid],
                "steps": [f"{route} — {requirement} — {proof}"[:900]],
            })
            fixed += 1
            notes = getattr(self, "_automatic_plan_repairs", None)
            if notes is None:
                notes = []
                self._automatic_plan_repairs = notes
            notes.append({"requirement": requirement, "capability": cid,
                          "files": list(files)})
            self._log("INFO", f"   ✅ auto-placed {cid}: {requirement[:90]}")
        return fixed

    def _repair_inert_plan(self, plan: dict) -> int:
        """Replace explicit placeholder instructions with full capability work."""
        caps = [c for c in (plan or {}).get("capabilities") or [] if isinstance(c, dict)]
        by_file = {}
        for cap in caps:
            for rel in cap.get("files") or []:
                by_file.setdefault(str(rel), []).append(cap)
        fixed = 0
        for phase in (plan or {}).get("phases") or []:
            if not isinstance(phase, dict):
                continue
            for spec in phase.get("files") or []:
                if not isinstance(spec, dict):
                    continue
                blob = " ".join(str(v) if not isinstance(v, list)
                                else " ".join(map(str, v)) for v in spec.values())
                if not self._INERT_PLAN_RE.search(blob):
                    continue
                rel = str(spec.get("path") or "")
                owners = by_file.get(rel) or []
                requirement = "; ".join(str(c.get("requirement") or "")
                                        for c in owners if c.get("requirement"))
                requirement = requirement or str(phase.get("goal") or phase.get("title") or "the planned behavior")
                proof = "; ".join(str(c.get("proof") or "")
                                  for c in owners if c.get("proof"))
                spec["purpose"] = f"Complete production implementation of {requirement}"[:900]
                for key in ("sections", "actions", "invariants"):
                    clean = [str(x) for x in (spec.get(key) or [])
                             if not self._INERT_PLAN_RE.search(str(x))]
                    spec[key] = clean
                spec.setdefault("sections", []).append(
                    f"Complete {requirement} with loading, empty, success, and error states"[:700])
                spec.setdefault("actions", []).append(
                    f"perform the real {requirement}; prove {proof or 'the durable visible result'}; show success/error toast feedback"[:900])
                spec.setdefault("invariants", []).append(
                    "No placeholder, coming-soon, TODO, or disabled-for-now behavior may ship")
                fixed += 1
                self._log("INFO", f"   🧩 replaced inert instructions in {rel} "
                                  "with the complete capability contract")
        return fixed

    @staticmethod
    def _gap_size(gaps: tuple) -> int:
        return sum(len(group) for group in gaps)

    def _converge_capability_map(self, messages: list, raw: str,
                                 source_reqs: list) -> str:
        """Use up to six focused replans, with deterministic convergence."""
        for round_no in range(1, self.CAPABILITY_REPLAN_ROUNDS + 1):
            self._repair_capability_map(self.plan)
            self._repair_inert_plan(self.plan)
            gaps = self._capability_gaps(self.plan, raw)
            if not any(gaps):
                return raw

            why = []
            if gaps[0]:
                why.append("source/Core requirements with no machine capability: " +
                           "; ".join(gaps[0]))
            if gaps[1]:
                why.append("e2e capabilities no workflow covers: " +
                           ", ".join(gaps[1]))
            if gaps[2]:
                why.append("capabilities with missing/unplanned files or inert work: " +
                           ", ".join(gaps[2]))
            self._log("WARN", f"   ⚠ capability convergence round {round_no}/"
                              f"{self.CAPABILITY_REPLAN_ROUNDS} — " +
                              " | ".join(why)[:500])
            details = list(getattr(self, "_last_gap_details", None) or [])
            current_json = json.dumps(self.plan, ensure_ascii=False, indent=2)
            prompt = (
                f"CAPABILITY REPAIR ROUND {round_no} OF {self.CAPABILITY_REPLAN_ROUNDS}.\n"
                "The normalized plan below is the current source of truth. Rewrite the WHOLE "
                "plan without dropping any existing task, route, file, capability, role, or "
                "workflow. Fix every listed gap. Each missing source/Core requirement needs a "
                "capability with observable proof and exact files that a task also builds. "
                "Every browser capability needs a workflow that actually performs it. Never "
                "use placeholders or disabled-for-now controls. Emit markdown + the complete "
                "JSON block again.\n\nGAPS:\n- " + "\n- ".join(why) +
                (("\n\nEXACT FILE DEFECTS:\n- " + "\n- ".join(details)) if details else "") +
                "\n\nCURRENT NORMALIZED PLAN JSON:\n```json\n" +
                current_json[:70_000] + "\n```"
            )

            candidate = None
            candidate_raw = ""
            try:
                buf = []
                repair_messages = messages[:2] + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": prompt},
                ]
                self._stream_capability_replan(
                    repair_messages, buf.append,
                    max(0.25, 0.55 - round_no * 0.04))
                candidate_raw = "".join(buf)
                candidate = self._extract_plan_json(candidate_raw)
            except Exception as exc:
                self._log("WARN", f"   ⚠ capability replan round {round_no} "
                                  f"failed: {exc}")

            if candidate and candidate.get("phases"):
                candidate["source_requirements"] = source_reqs
                if getattr(self, "_known_fr", None):
                    self._repair_requirement_coverage(
                        candidate, self._uncovered(candidate))
                self._repair_capability_map(candidate)
                self._repair_inert_plan(candidate)
                candidate_gaps = self._capability_gaps(candidate, candidate_raw)
                if self._gap_size(candidate_gaps) < self._gap_size(gaps):
                    self.plan, raw, gaps = candidate, candidate_raw, candidate_gaps
                    self._log("INFO", f"   ✅ capability replan round {round_no} "
                                      "improved the proof map")
                else:
                    self._log("INFO", f"   ↻ model round {round_no} repeated "
                                      "the same proof map — controller repair "
                                      "is taking over")

            # The controller creates actual builder instructions and proof edges;
            # it never marks an absent feature as passed.
            before = self._capability_gaps(self.plan, raw)
            fixed = self._repair_missing_capabilities(self.plan, before[0])
            fixed += self._repair_capability_map(self.plan)
            fixed += self._repair_inert_plan(self.plan)
            after = self._capability_gaps(self.plan, raw)
            if fixed and self._gap_size(after) < self._gap_size(before):
                self._log("INFO", f"   ✅ controller auto-repair placed {fixed} "
                                  f"missing proof edge(s) in round {round_no}")
            if not any(after):
                return raw
        return raw
