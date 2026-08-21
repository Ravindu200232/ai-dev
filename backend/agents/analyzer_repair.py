"""Repair wrapper built from focused analyzer mixins."""
from .analyzer_repair_findings import AnalyzerRepairFindingsMixin
from .analyzer_repair_apply import AnalyzerRepairApplyMixin
from .analyzer_repair_run import AnalyzerRepairRunMixin

class AnalyzerRepairMixin(AnalyzerRepairFindingsMixin, AnalyzerRepairApplyMixin, AnalyzerRepairRunMixin):
    pass
