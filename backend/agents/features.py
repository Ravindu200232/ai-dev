"""Wrapper for the split FeaturesAgent implementation."""
from .features_common import *
from .features_planning import FeaturesAgentPlanningMixin
from .features_apply import FeaturesAgentApplyMixin
from .features_audit import FeaturesAgentAuditMixin


class FeaturesAgent(FeaturesAgentPlanningMixin, FeaturesAgentApplyMixin, FeaturesAgentAuditMixin, FeaturesAgentBase):
    pass

# Preserve the public surface of the old module.
__all__ = ['FeatureSpec', 'FeaturesAgent', 'LOCAL_IMPORT_RE', 'MAX_FILES', 'MAX_PACKAGES', 'MAX_READS', 'PLAN_LINE_RE', 'PLAN_SYSTEM', 'REPAIR_SYSTEM', 'log']
