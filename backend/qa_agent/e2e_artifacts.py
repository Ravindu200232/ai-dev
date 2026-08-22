"""Persistent, auditable Playwright artifacts for planner-driven E2E journeys."""
from __future__ import annotations

import hashlib

from .e2e_common import *
from .e2e_contract import capability_contract
from .flows import Step


def _rows(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    out = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


class E2EArtifactsMixin:
    """Write Playwright fixtures/plans and reuse only source-identical scenarios."""

    CACHE_VERSION = 1

    @staticmethod
    def _journey_slug(journey: dict) -> str:
        return Scenario(title=str((journey or {}).get("title") or "journey")).slug()

    def _write_artifact(self, rel: str, body: str, *, announce: bool = True) -> bool:
        """Write only when content changed, avoiding duplicate `wrote` events."""
        fp = self.project_dir / rel
        try:
            if fp.is_file() and fp.read_text(encoding="utf-8") == body:
                return False
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(body, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._log("WARN", f"   ⚠ could not write {rel}: {exc}")
            return False
        if announce:
            size = f"{len(body) / 1024:.1f}KB" if len(body) >= 1024 else f"{len(body)}B"
            self._fire("on_file_written", rel, size, body)
            self._log("INFO", f"   🎭 {rel}")
        return True

    def ensure_playwright_artifacts(self, journeys: list[dict]) -> None:
        """Create the shared role fixture, seed test, config and journey plans."""
        accounts = {}
        for account in self.accounts():
            role = str(account.get("role") or "").strip().lower()
            if not role or role in accounts:
                continue
            if account.get("email") and account.get("password"):
                accounts[role] = {
                    "role": role,
                    "email": str(account.get("email") or ""),
                    "password": str(account.get("password") or ""),
                }

        fixture_accounts = json.dumps(accounts, ensure_ascii=False, indent=2)
        fixture = f"""// Written by AgentForge. One exact fixture per role; no role fallback.
import {{ test as base, expect }} from '@playwright/test'

export const roleAccounts = Object.freeze({fixture_accounts})

export function accountFor(role) {{
  return roleAccounts[String(role || '').trim().toLowerCase()] || null
}}

export const test = base.extend({{
  agentforgeRole: ['', {{ option: true }}],
  roleAccount: async ({{ agentforgeRole }}, use) => {{
    const account = accountFor(agentforgeRole)
    if (agentforgeRole && !account) {{
      throw new Error(`No seeded account exists for exact role: ${{agentforgeRole}}`)
    }}
    await use(account)
  }},
}})

export {{ expect }}
"""
        seed = """// Persistent seed test used as the example/bootstrap for generated journeys.
// AgentForge resets and re-seeds the project database before dependent journeys.
import { test, expect } from './fixtures.js'

test('seed environment is ready', async ({ page }) => {
  const response = await page.goto('/', { waitUntil: 'domcontentloaded' })
  expect(response?.status() ?? 200).toBeLessThan(400)
})
"""
        self._write_artifact("tests/e2e/fixtures.js", fixture)
        self._write_artifact("tests/e2e/seed.spec.js", seed)
        cfg = self.project_dir / "playwright.config.js"
        if not cfg.is_file():
            self._write_artifact("playwright.config.js", PLAYWRIGHT_CONFIG)
        for journey in journeys:
            self.write_journey_plan(journey)

    def write_journey_plan(self, journey: dict, scenario: Scenario = None) -> str:
        """Save one human-readable test plan per journey under ``specs/``."""
        journey = journey or {}
        contract = journey.get("contract") or capability_contract(self.arch, journey)
        slug = self._journey_slug(journey)
        role = str(journey.get("role") or "").strip() or "SIGNED OUT"
        preconditions = _rows(journey.get("preconditions"))
        expected = (_rows(journey.get("expected_results"))
                    or _rows(contract.get("proofs")))
        routes = _rows(journey.get("served_routes") or journey.get("routes"))
        source_files = _rows(contract.get("source_files"))

        lines = [
            f"# {journey.get('title') or 'E2E journey'}",
            "",
            f"- Actor: `{role}`",
            f"- Covers: `{', '.join(journey.get('covers') or []) or 'route/workflow contract'}`",
            f"- Generated spec: `tests/e2e/{slug}.spec.js`",
            "",
            "## Pre-journey setup",
            "",
        ]
        if preconditions:
            lines.extend(f"- {item}" for item in preconditions)
        pre = journey.get("pre_journey") or {}
        if pre:
            lines.extend(["", "```json", json.dumps(pre, ensure_ascii=False, indent=2), "```"])
        if not preconditions and not pre:
            lines.append("- Start from the deterministic project seed.")

        def section(title, values):
            lines.extend(["", f"## {title}", ""])
            if values:
                lines.extend(f"{n}. {value}" for n, value in enumerate(values, 1))
            else:
                lines.append("- None declared.")

        section("Planned steps", _rows(journey.get("steps")))
        section("Expected results", expected)
        section("Served routes", routes)
        section("Source files", source_files)
        section("Required proofs", _rows(contract.get("proofs")))
        if scenario is not None:
            section("Executable scenario", [step.describe() for step in scenario.steps])
        lines.extend(["", "Skip, block, or an unexecuted step is not a pass.", ""])
        rel = f"specs/{slug}.md"
        self._write_artifact(rel, "\n".join(lines))
        return rel

    def _cache_path(self) -> Path:
        return self.project_dir / ".agentforge" / "qa" / "e2e-scenarios.json"

    def _load_cache(self) -> dict:
        try:
            data = json.loads(self._cache_path().read_text(encoding="utf-8"))
            if int(data.get("version") or 0) == self.CACHE_VERSION:
                return data
        except (OSError, TypeError, ValueError) as exc:
            log.debug(f"E2E scenario cache: {exc}")
        return {"version": self.CACHE_VERSION, "journeys": {}}

    def _journey_fingerprint(self, journey: dict) -> str:
        """Hash planner facts plus the generated source that implements the journey."""
        journey = journey or {}
        contract = journey.get("contract") or capability_contract(self.arch, journey)
        relevant = {
            key: journey.get(key) for key in (
                "title", "role", "covers", "steps", "preconditions",
                "pre_journey", "expected_results", "routes", "served_routes",
            )
        }
        relevant["contract"] = contract
        files = []
        for rel in list(contract.get("source_files") or []):
            rel = str(rel or "").replace("\\", "/")
            if rel and rel not in files:
                files.append(rel)
        try:
            for rel in self._journey_pages(journey):
                if rel and rel not in files:
                    files.append(rel)
        except Exception as exc:  # noqa: BLE001 - analyzer/project adapters vary
            log.debug(f"E2E journey source paths: {exc}")
        sources = {}
        arch_files = getattr(self.arch, "files", None) or {}
        for rel in files[:40]:
            body = arch_files.get(rel)
            if body is None:
                try:
                    body = (self.project_dir / rel).read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    body = "<missing>"
            sources[rel] = str(body)
        payload = json.dumps({"journey": relevant, "sources": sources},
                             ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _scenario_data(sc: Scenario) -> dict:
        return {
            "title": sc.title,
            "role": sc.role,
            "dropped": list(sc.dropped or []),
            "steps": [{
                "verb": step.verb,
                "value": step.value,
                "line": step.line,
                "selector": ({
                    "kind": step.selector.kind,
                    "pattern": step.selector.pattern,
                    "flags": step.selector.flags,
                    "role": step.selector.role,
                    "is_regex": step.selector.is_regex,
                } if step.selector else None),
            } for step in sc.steps],
        }

    @staticmethod
    def _scenario_from_data(data: dict) -> Scenario:
        steps = []
        for row in data.get("steps") or []:
            raw = row.get("selector")
            selector = Selector(**raw) if isinstance(raw, dict) else None
            steps.append(Step(verb=str(row.get("verb") or ""), selector=selector,
                              value=str(row.get("value") or ""),
                              line=int(row.get("line") or 0)))
        return Scenario(title=str(data.get("title") or "user flow"),
                        role=str(data.get("role") or ""), steps=steps,
                        dropped=list(data.get("dropped") or []))

    def cached_scenario(self, journey: dict):
        """Return a prior green scenario only when its inputs and source match."""
        key = self._journey_slug(journey)
        row = (self._load_cache().get("journeys") or {}).get(key) or {}
        if row.get("fingerprint") != self._journey_fingerprint(journey):
            return None
        try:
            scenario = self._scenario_from_data(row.get("scenario") or {})
        except Exception as exc:  # noqa: BLE001
            log.debug(f"cached E2E scenario {key}: {exc}")
            return None
        if scenario.is_runnable():
            return None
        self._log("INFO", f"   ♻ unchanged E2E journey — reusing tests/e2e/{key}.spec.js")
        return scenario

    def remember_generated_scenario(self, journey: dict, scenario: Scenario) -> None:
        """Persist only a green scenario; callers invoke this after real execution."""
        data = self._load_cache()
        key = self._journey_slug(journey)
        data.setdefault("journeys", {})[key] = {
            "fingerprint": self._journey_fingerprint(journey),
            "spec": scenario.spec_path(),
            "scenario": self._scenario_data(scenario),
        }
        body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self._write_artifact(".agentforge/qa/e2e-scenarios.json", body,
                             announce=False)
        self.write_journey_plan(journey, scenario)


__all__ = ["E2EArtifactsMixin"]
