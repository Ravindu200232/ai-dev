"""BugFixerAgent wrapper."""
from .bugfixer_common import *
from .bugfixer_scope import BugFixerScopeMixin
from .bugfixer_apply import BugFixerApplyMixin
from .bugfixer_prompt import BugFixerPromptMixin

class BugFixerAgent(BugFixerScopeMixin, BugFixerApplyMixin, BugFixerPromptMixin):
    pass


# Preserve the public surface of the old module.
__all__ = ['BugFixerAgent', 'CALL_BUDGET', 'CODE_CHANGE_FRAC', 'CODE_CHANGE_MIN', 'FixVerdict', 'HTTP_METHODS', 'MAX_APP_FILES', 'NEVER_CODE', 'RUNTIME_CHANGE_FRAC', 'RUNTIME_CHANGE_MIN', 'RUNTIME_MAX_FILES', 'RUNTIME_SYSTEM', 'SYSTEM', 'TEMPERATURE', 'VERDICT_HARNESS', 'VERDICT_RE', 'WEAKENED_RE', 'log']
