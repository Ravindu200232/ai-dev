"""UnitTestAuthor wrapper."""
from .author_common import *
from .author_write import UnitAuthorWriteMixin
from .author_repair import UnitAuthorRepairMixin
from .author_guards import UnitAuthorGuardMixin

class UnitTestAuthor(UnitAuthorWriteMixin, UnitAuthorRepairMixin, UnitAuthorGuardMixin):
    pass


# Preserve the public surface of the old module.
__all__ = ['CALL_BUDGET', 'MAX_READS', 'MAX_VERIFY_ROUNDS', 'QA_FIX_WORKERS', 'SETTLE_MAX_S', 'SYSTEM', 'TEMPERATURE', 'UnitTestAuthor', 'log']
