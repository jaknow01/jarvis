"""Compatibility shim — moved to lib.core.logger. Kept during the lib/ refactor so
existing imports keep working; remove once all callers import from lib.core."""
from lib.core.logger import Logger, LOGS_DIR  # noqa: F401
