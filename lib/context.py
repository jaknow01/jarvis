"""Compatibility shim — moved to lib.core.env. Kept during the lib/ refactor so
existing imports keep working; remove once all callers import from lib.core."""
from lib.core.env import (  # noqa: F401
    environment_preamble,
    TIMEZONE,
    BASE_CURRENCY,
    LOCALE,
)
