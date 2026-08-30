"""Unit tests for the watch source registry + three-state read (lib/agents/scheduler_agent/watch_sources.py).

Uses fake fetchers — no network. Async entry points run via asyncio.run."""
import asyncio

from lib.agents.scheduler_agent.watch_sources import (
    fetch_source,
    is_known,
    known_sources,
    register_source,
)


def test_default_v1_sources_registered():
    assert is_known("finance.fx") and is_known("finance.quote")
    assert "finance.fx" in known_sources()


def test_unknown_source_resolves_to_unknown():
    state, data = asyncio.run(fetch_source("nope.nope", {}))
    assert state == "unknown" and data is None


def test_ok_state_passes_data_through():
    async def fake(_p):
        return {"rate": 3.5}
    register_source("test.ok", fake)
    state, data = asyncio.run(fetch_source("test.ok", {}))
    assert state == "ok" and data == {"rate": 3.5}


def test_error_dict_maps_to_unknown():
    async def fake(_p):
        return {"Error": "boom"}
    register_source("test.err", fake)
    state, data = asyncio.run(fetch_source("test.err", {}))
    assert state == "unknown" and data is None


def test_fetch_exception_maps_to_unknown():
    async def fake(_p):
        raise RuntimeError("network down")
    register_source("test.raise", fake)
    state, data = asyncio.run(fetch_source("test.raise", {}))
    assert state == "unknown" and data is None


def test_params_are_passed_to_fetcher():
    seen = {}

    async def fake(p):
        seen.update(p)
        return {"ok": True}
    register_source("test.params", fake)
    asyncio.run(fetch_source("test.params", {"symbol": "AAPL"}))
    assert seen == {"symbol": "AAPL"}
