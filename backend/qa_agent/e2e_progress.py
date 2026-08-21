"""Convergence policy for agentic E2E repair loops."""
from __future__ import annotations

import re


def normalize_message(text: str) -> str:
    """Return stable failure text for convergence tracking."""
    value = str(text or "").lower()
    value = re.sub(r"\b[0-9a-f]{24}\b", "<oid>", value)
    value = re.sub(r"https?://(?:127\.0\.0\.1|localhost):\d+", "<base>", value)
    value = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ms|s)\b", "<time>", value)
    value = re.sub(r"\b\d{3,}\b", "<n>", value)
    return " ".join(value.split())[:260]


def failure_signature(failures) -> tuple:
    """Return a hashable description of the current obstruction."""
    rows = []
    for failure in (failures or [])[:6]:
        rows.append((
            str(getattr(failure, "kind", "") or "").upper(),
            str(getattr(failure, "target", "") or "").replace("\\", "/"),
            normalize_message(getattr(failure, "name", "")),
            normalize_message(getattr(failure, "message", "")),
        ))
    return tuple(rows)


def failure_severity(failures) -> int:
    """Return a coarse symptom rank; this alone never proves progress."""
    rank = {
        "CRASH": 4,
        "BEHAVIOR": 3,
        "RUNTIME": 3,
        "ASSERTION": 2,
        "SELECTOR": 1,
    }
    return max((rank.get(str(getattr(f, "kind", "") or "").upper(), 2)
                for f in (failures or [])), default=0)


def measure_progress(before, after, seen_signatures: set, *,
                     before_step: int = -1, after_step: int = -1) -> tuple[bool, str]:
    """Decide whether a repair moved the actual business journey forward."""
    if before and not after:
        return True, "flow passed"
    if not after:
        return False, ""
    if after_step > before_step >= -1:
        return True, f"scenario step {before_step + 1}→{after_step + 1}"
    if before_step >= 0 and after_step >= 0 and after_step < before_step:
        return False, f"browser regressed from step {before_step + 1} to {after_step + 1}"
    if len(after) < len(before) and after_step >= before_step:
        return True, f"failures {len(before)}→{len(after)}"

    old_severity = failure_severity(before)
    new_severity = failure_severity(after)
    if new_severity < old_severity:
        return False, f"same scenario step; symptom severity {old_severity}→{new_severity}"

    signature = failure_signature(after)
    if signature and signature not in seen_signatures:
        return False, "different symptom at the same scenario checkpoint"
    return False, "same/previously-seen obstruction"


MIN_REPAIR_ROUNDS = 2

def stop_after_no_progress(round_no: int, progressed: bool, minimum_rounds: int = MIN_REPAIR_ROUNDS,
                           *, exhausted: bool = False) -> bool:
    """Stop when evidence is exhausted, otherwise honor the normal repair floor."""
    if exhausted and not bool(progressed):
        return True
    try:
        round_no = int(round_no)
        minimum_rounds = max(1, int(minimum_rounds))
    except Exception:
        return False
    return round_no >= minimum_rounds and not bool(progressed)


def extend_round_budget(round_no: int, budget: int, hard_cap: int, bonus: int,
                        progressed: bool) -> int:
    """Grant more repair rounds only when the browser earned them."""
    try:
        round_no, budget, hard_cap, bonus = map(int,
                                                (round_no, budget, hard_cap, bonus))
    except Exception:
        return budget
    if not progressed or hard_cap <= 0 or bonus <= 0:
        return budget
    if round_no < budget or budget >= hard_cap:
        return budget
    return min(hard_cap, budget + bonus)


__all__ = ["normalize_message", "failure_signature", "failure_severity", "measure_progress", "extend_round_budget", "stop_after_no_progress", "MIN_REPAIR_ROUNDS"]
