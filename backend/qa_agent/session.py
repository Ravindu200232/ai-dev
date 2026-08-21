"""Wrapper for the split QASession implementation."""
from .session_common import *
from .session_queue import QASessionQueueMixin
from .session_files import QASessionFilesMixin


class QASession(QASessionQueueMixin, QASessionFilesMixin, QASessionBase):
    pass

# Preserve the public surface of the old module.
__all__ = ['HELPER_HOMES', 'HELPER_SPIES', 'MANIFEST', 'QASession', 'QA_AUTHOR_WORKERS', 'QA_DIR', 'QA_MAX_COMMANDS', 'STUBBED_MODULES', 'TEST_PATH_RE', 'add_helper_imports', 'drop_redundant_mocks', 'ensure_mocks', 'log', 'mock_line', 'required_mocks']
