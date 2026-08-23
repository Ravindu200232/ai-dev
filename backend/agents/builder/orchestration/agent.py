"""Next.js builder agent assembled from focused mixins."""
from .core import *
from agents.builder.prompts.catalog import *
from .runtime import ArchitectRuntimeMixin
from .memory import ArchitectMemoryMixin
from .writes import ArchitectWriteMixin
from agents.planner.execution import ArchitectPlanningMixin
from agents.planner.normalization import ArchitectPlanNormalizeMixin
from agents.builder.scaffolding.base import ArchitectScaffoldMixin
from agents.builder.scaffolding.next import ArchitectNextScaffoldMixin
from agents.builder.workflow.build import ArchitectBuildMixin
from agents.builder.workflow.ledgers import ArchitectLedgerMixin
from .turns import ArchitectTurnMixin
from agents.builder.workflow.symbols import ArchitectSymbolMixin
from agents.builder.workflow.rules import ArchitectNextRulesMixin
from agents.builder.workflow.boundaries import ArchitectBoundaryMixin
from agents.builder.workflow.delivery import ArchitectDeliveryMixin
from .persistence import ArchitectPersistenceMixin


class ArchitectAgent(
    ArchitectRuntimeMixin, ArchitectMemoryMixin, ArchitectWriteMixin,
    ArchitectPlanningMixin, ArchitectPlanNormalizeMixin, ArchitectScaffoldMixin,
    ArchitectNextScaffoldMixin, ArchitectBuildMixin, ArchitectLedgerMixin,
    ArchitectTurnMixin, ArchitectSymbolMixin, ArchitectNextRulesMixin,
    ArchitectBoundaryMixin, ArchitectDeliveryMixin, ArchitectPersistenceMixin,
):
    """Plan, scaffold, generate, repair and persist a full application."""
    pass



__all__ = ['ArchitectAgent', 'CHARS_PER_TOKEN', 'CMD_RE', 'FENCE_RE', 'FileStreamParser', 'HISTORY_BUDGET', 'NEXT_BUILDER_SYSTEM', 'NEXT_PLANNER_SYSTEM', 'NEXT_STACK_RULES', 'OPEN_RE', 'PARTIAL_OPEN_RE', 'PROMPTS', 'SNAPSHOT_CLOSE', 'SNAPSHOT_OPEN', 'SNAPSHOT_RE', 'STUB_MARK', 'WRITE_FILE_TOOL', 'log']
