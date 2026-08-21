"""ArchitectAgent wrapper built from maintainable mixins."""
from .architect_core import *
from .architect_prompts import *
from .architect_runtime import ArchitectRuntimeMixin
from .architect_memory import ArchitectMemoryMixin
from .architect_writes import ArchitectWriteMixin
from .architect_planning import ArchitectPlanningMixin
from .architect_plan_normalize import ArchitectPlanNormalizeMixin
from .architect_scaffold import ArchitectScaffoldMixin
from .architect_next_scaffold import ArchitectNextScaffoldMixin
from .architect_build import ArchitectBuildMixin
from .architect_ledgers import ArchitectLedgerMixin
from .architect_turns import ArchitectTurnMixin
from .architect_symbols import ArchitectSymbolMixin
from .architect_next_rules import ArchitectNextRulesMixin
from .architect_boundaries import ArchitectBoundaryMixin
from .architect_delivery import ArchitectDeliveryMixin
from .architect_persistence import ArchitectPersistenceMixin


class ArchitectAgent(
    ArchitectRuntimeMixin, ArchitectMemoryMixin, ArchitectWriteMixin,
    ArchitectPlanningMixin, ArchitectPlanNormalizeMixin, ArchitectScaffoldMixin,
    ArchitectNextScaffoldMixin, ArchitectBuildMixin, ArchitectLedgerMixin,
    ArchitectTurnMixin, ArchitectSymbolMixin, ArchitectNextRulesMixin,
    ArchitectBoundaryMixin, ArchitectDeliveryMixin, ArchitectPersistenceMixin,
):
    """Plan, scaffold, generate, repair and persist a full application."""
    pass



# Preserve the public surface of the old module.
__all__ = ['ArchitectAgent', 'CHARS_PER_TOKEN', 'CMD_RE', 'FENCE_RE', 'FileStreamParser', 'HISTORY_BUDGET', 'NEXT_BUILDER_SYSTEM', 'NEXT_PLANNER_SYSTEM', 'NEXT_STACK_RULES', 'OPEN_RE', 'PARTIAL_OPEN_RE', 'PROMPTS', 'SNAPSHOT_CLOSE', 'SNAPSHOT_OPEN', 'SNAPSHOT_RE', 'STUB_MARK', 'VITE_BUILDER_SYSTEM', 'VITE_PLANNER_SYSTEM', 'VITE_STACK_RULES', 'WRITE_FILE_TOOL', 'log']
