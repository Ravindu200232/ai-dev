"""Agentic E2E debugger wrapper."""
from .debugger_common import *
from .debugger_investigate import DebuggerInvestigateMixin
from .debugger_reasoning import DebuggerReasoningMixin

class AgenticE2EDebugger(DebuggerInvestigateMixin, DebuggerReasoningMixin):
    pass


# Preserve the public surface of the old module.
__all__ = ['AgenticE2EDebugger', 'DebugNotebook', 'MAX_TOOL_TURNS', 'SYSTEM', 'TOOL_RESULT_CHARS', 'VERDICTS', 'log']
