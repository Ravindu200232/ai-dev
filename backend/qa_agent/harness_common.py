"""Everything a generated test needs to run, written by Python."""
import logging
import re
import textwrap
import threading
from pathlib import Path

log = logging.getLogger("qa.harness")


NPM_LOCK = threading.RLock()


def npm_busy() -> bool:
    """True when something is installing right now."""
    if NPM_LOCK.acquire(blocking=False):
        NPM_LOCK.release()
        return False
    return True


DEV_DEPS = ["vitest@3", "jsdom@25",
            "@testing-library/react@16", "@testing-library/jest-dom@6",

            "@testing-library/user-event@14"]

CONFIG = "vitest.config.mjs"
SETUP = "tests/setup.js"
HELPERS = "tests/helpers"

__all__ = [name for name in globals() if not name.startswith("__")]
