"""Bug fixer and runtime repair domain."""
from .common import *
from .scope import BugFixerScopeMixin
from .apply import BugFixerApplyMixin
from .prompt import BugFixerPromptMixin

class BugFixerAgent(BugFixerScopeMixin, BugFixerApplyMixin, BugFixerPromptMixin):
    pass


__all__ = ['BugFixerAgent', 'CALL_BUDGET', 'CODE_CHANGE_FRAC', 'CODE_CHANGE_MIN', 'FixVerdict', 'HTTP_METHODS', 'MAX_APP_FILES', 'NEVER_CODE', 'RUNTIME_CHANGE_FRAC', 'RUNTIME_CHANGE_MIN', 'RUNTIME_MAX_FILES', 'RUNTIME_SYSTEM', 'SYSTEM', 'TEMPERATURE', 'VERDICT_HARNESS', 'VERDICT_RE', 'WEAKENED_RE', 'log']
