"""TestHarness wrapper."""
from .harness_common import *
from .harness_mocks import HarnessMockMixin
from .harness_install import HarnessInstallMixin

class TestHarness(HarnessMockMixin, HarnessInstallMixin):
    pass


# Preserve the public surface of the old module.
__all__ = ['CONFIG', 'DEV_DEPS', 'HELPERS', 'NPM_LOCK', 'SETUP', 'TestHarness', 'log', 'npm_busy']
