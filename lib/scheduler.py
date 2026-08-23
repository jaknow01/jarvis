"""Compatibility shim — moved to lib.domain.scheduler. Kept during the lib/ refactor
so existing imports keep working; remove once all callers import from lib.domain."""
from lib.domain.scheduler import *  # noqa: F401,F403
from lib.domain.scheduler import (  # noqa: F401  (explicit: private names not re-exported by *)
    _select_backend,
    _JobStoreProxy,
    _first_run,
    JOBS_PATH,
    DEFAULT_TIMEZONE,
)
