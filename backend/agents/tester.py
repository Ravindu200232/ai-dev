"""Wrapper for the split TesterAgent implementation."""
from .tester_common import *
from .tester_routes import TesterAgentRoutesMixin
from .tester_browser import TesterAgentBrowserMixin


class TesterAgent(TesterAgentRoutesMixin, TesterAgentBrowserMixin, TesterAgentBase):
    pass

# Preserve the public surface of the old module.
__all__ = ['OVERLAY_SIGNAL_RE', 'STACKS', 'TesterAgent', 'elog', 'etest', 'log', 'overlay_error', 'set_emit']
