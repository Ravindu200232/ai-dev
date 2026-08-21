"""Wrapper for the split MongoManager implementation."""
from .mongo_common import *
from .mongo_install import MongoManagerInstallMixin
from .mongo_lifecycle import MongoManagerLifecycleMixin
from .mongo_data import MongoManagerDataMixin


class MongoManager(MongoManagerInstallMixin, MongoManagerLifecycleMixin, MongoManagerDataMixin, MongoManagerBase):
    pass


MONGO = MongoManager()

# Preserve the public surface of the old module.
__all__ = ['BUDGET_S', 'DEFAULT_PORT', 'FALLBACK_URLS', 'FALLBACK_VERSION', 'FEED_URL', 'HOME', 'MONGO', 'MongoManager', 'db_name_for', 'get_uri_override', 'log']
