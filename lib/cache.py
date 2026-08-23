"""Compatibility shim — moved to lib.core.cache. Kept during the lib/ refactor so
existing imports (`from lib.cache import Cache, Ctx`) keep working; remove once all
callers import from lib.core."""
from lib.core.cache import Cache, Ctx  # noqa: F401
