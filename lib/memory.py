"""Compatibility shim — moved to lib.domain.memory. Kept during the lib/ refactor so
existing imports keep working; remove once all callers import from lib.domain."""
from lib.domain.memory import *  # noqa: F401,F403
from lib.domain.memory import (  # noqa: F401  (explicit: private names not re-exported by *)
    _select_backend,
    _MemoryProxy,
    MEMORY_PATH,
)
