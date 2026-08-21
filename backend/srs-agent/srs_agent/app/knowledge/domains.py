"""Business-domain catalog API."""
from .domains_core import (
    GENERIC_KEY, GUEST_ROLE, SUPER_ADMIN, f, fk, pk, status, timestamps,
    universal_tables,
)
from .domains_generic import GENERIC_DOMAIN
from .domains_runtime import DOMAIN_LIBRARY, classify_domain, get_domain

__all__ = [
    "GENERIC_KEY", "GUEST_ROLE", "SUPER_ADMIN", "DOMAIN_LIBRARY",
    "GENERIC_DOMAIN", "universal_tables", "get_domain", "classify_domain",
    "pk", "fk", "f", "timestamps", "status",
]
