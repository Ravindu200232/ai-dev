"""Runtime knobs for evidence-first E2E convergence."""
from __future__ import annotations

import os


def env_int(name: str, default: int, lo: int = 0, hi: int = 10) -> int:
    """Read a bounded integer without letting a bad environment break startup."""
    try:
        return max(lo, min(hi, int(os.getenv(name, str(default)) or default)))
    except Exception:
        return default


E2E_BASE_FIX = env_int("AGENTFORGE_E2E_REPAIR_ATTEMPTS", 2, 1, 4)
E2E_HARD_FIX = env_int("AGENTFORGE_E2E_HARD_REPAIR_ATTEMPTS", 6, 2, 10)
E2E_PROGRESS_BONUS = env_int("AGENTFORGE_E2E_PROGRESS_BONUS", 2, 1, 4)
E2E_AUTHOR_REWRITE_ATTEMPTS = env_int("AGENTFORGE_E2E_AUTHOR_REWRITES", 2, 0, 3)
E2E_GLOBAL_REPAIR_ATTEMPTS = env_int("AGENTFORGE_E2E_GLOBAL_REPAIR_ATTEMPTS", 1, 0, 2)
E2E_RETRY_BLOCKED = os.getenv(
    "AGENTFORGE_E2E_RETRY_BLOCKED", "1"
).strip().lower() in ("1", "true", "yes", "on")

E2E_FINAL_CLEAN_ROOM = os.getenv(
    "AGENTFORGE_E2E_FINAL_CLEAN_ROOM", "1"
).strip().lower() in ("1", "true", "yes", "on")
E2E_FINAL_REPAIR_ATTEMPTS = env_int("AGENTFORGE_E2E_FINAL_REPAIR_ATTEMPTS", 1, 0, 2)
E2E_FINAL_RESET_DB = os.getenv(
    "AGENTFORGE_E2E_FINAL_RESET_DB", "1"
).strip().lower() in ("1", "true", "yes", "on")


__all__ = [
    "E2E_AUTHOR_REWRITE_ATTEMPTS",
    "E2E_BASE_FIX",
    "E2E_GLOBAL_REPAIR_ATTEMPTS",
    "E2E_FINAL_CLEAN_ROOM",
    "E2E_FINAL_REPAIR_ATTEMPTS",
    "E2E_FINAL_RESET_DB",
    "E2E_HARD_FIX",
    "E2E_PROGRESS_BONUS",
    "E2E_RETRY_BLOCKED",
    "env_int",
]
