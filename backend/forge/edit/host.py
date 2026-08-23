"""Reading a host agent's own settings — its model, and how much context."""
from __future__ import annotations

from ..tokens import CHARS_PER_TOKEN

BUDGET_FLOOR = 8_000
BUDGET_DEFAULT = 20_000
EDIT_TIMEOUT = 150


def edit_model(arch, name: str = ""):
    """The model an edit runs on — the host's own, if it has one to lend.

    `arch.forge_model` lets a caller (or a test) supply a configured model
    rather than have one built from a name.
    """
    lent = getattr(arch, "forge_model", None)
    if lent is not None:
        return lent
    from ..llm import Model
    return Model(str(name or getattr(arch, "model", "") or ""),
                 timeout=int(getattr(arch, "EDIT_TIMEOUT", EDIT_TIMEOUT)
                             or EDIT_TIMEOUT))


def edit_budget(arch, chars: int = 0) -> int:
    """The host's character budget, as the token budget forge works in."""
    if not chars:
        try:
            chars = int(arch._budget_chars())
        except Exception:                                          # noqa: BLE001
            chars = 0
    if not chars:
        return BUDGET_DEFAULT
    return max(BUDGET_FLOOR, chars // CHARS_PER_TOKEN)


def project_dir(arch) -> str:
    """Where the host's project lives, or the working directory."""
    return str(getattr(arch, "project_dir", None) or ".")
