"""Named data-source registry for conditional watches (three-state reads).

Maps a ``source-id`` to a deterministic fetcher returning a ``dict``, reusing the existing
subagent data-fetching tools — **no LLM/agent run is involved**. ``fetch_source`` wraps a
fetch into a three-state result:

    ("ok", data)        - a clean data dict
    ("unknown", None)   - unknown source, the fetch raised, or it returned an error dict
                          (e.g. {"Error": ...}); never mistaken for a `false` predicate

The ``ctx`` argument of the underlying tools is decorative (``app_context`` reads a
contextvar, not the argument), so ctx-free sources are called with ``ctx=None``.

This module lives in the ``agents`` layer (alongside the ``scheduler_agent`` that owns
watches) because it reuses other agents' data fetchers — an agents->agents import — and is
consumed both by the ``create_watch`` tool (agents) and the scheduler runner (runtime). The
*catalog* of valid source-ids is owned by the domain layer (``WATCH_SOURCES``); this module
provides one fetcher per catalog entry and asserts it covers exactly that catalog.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from lib.domain import WATCH_SOURCES

logger = logging.getLogger(__name__)

Fetcher = Callable[[dict], Awaitable[dict]]

# source-id -> async fetcher(params) -> dict
SOURCES: dict[str, Fetcher] = {}


def register_source(source_id: str, fetcher: Fetcher) -> None:
    SOURCES[source_id] = fetcher


def known_sources() -> list[str]:
    return sorted(SOURCES)


def is_known(source_id: str) -> bool:
    return source_id in SOURCES


def _is_error(data) -> bool:
    return isinstance(data, dict) and "Error" in data


async def fetch_source(source_id: str, params: Optional[dict]) -> tuple[str, Optional[dict]]:
    """Fetch ``source_id`` with ``params`` as a three-state read. Never raises.

    Returns ``("ok", data)`` on a clean dict, or ``("unknown", None)`` when the source is
    unknown, the fetch raises, or it returns an error dict.
    """
    fetcher = SOURCES.get(source_id)
    if fetcher is None:
        logger.error("watch source '%s' is not registered", source_id)
        return "unknown", None
    try:
        data = await fetcher(params or {})
    except Exception as e:  # noqa: BLE001 - a fetch failure is 'unknown', never a crash
        logger.warning("watch source '%s' fetch failed: %s", source_id, e)
        return "unknown", None
    if _is_error(data):
        logger.info("watch source '%s' returned an error result", source_id)
        return "unknown", None
    return "ok", data


# --- v1 sources: reuse existing (ctx-free) subagent fetchers --------------------
def _register_default_sources() -> None:
    """Register the initial v1 source set (finance FX + stock/index quote).

    The finance tools take a decorative ``ctx`` they never use, so we call their
    underlying coroutine (``.async_fn``) with ``ctx=None``.
    """
    from lib.agents.finance_agent import get_exchange_rate, get_stock_quote

    async def finance_fx(params: dict) -> dict:
        return await get_exchange_rate.async_fn(
            None,
            foreign_currency=params["foreign_currency"],
            base_currency=params.get("base_currency", "PLN"),
        )

    async def finance_quote(params: dict) -> dict:
        return await get_stock_quote.async_fn(None, symbol=params["symbol"])

    register_source("finance.fx", finance_fx)
    register_source("finance.quote", finance_quote)

    # Keep the runtime fetchers and the domain catalog in lock-step.
    missing = set(WATCH_SOURCES) - set(SOURCES)
    extra = set(SOURCES) - set(WATCH_SOURCES)
    if missing or extra:
        logger.error(
            "watch source registry out of sync with WATCH_SOURCES catalog "
            "(missing fetchers: %s; uncatalogued: %s)", sorted(missing), sorted(extra)
        )


_register_default_sources()
