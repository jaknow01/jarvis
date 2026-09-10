"""Tests for principal-scoped context (`lib/core/cache.py`): the Ctx group/user split,
the group-scope cache, and no cross-principal bleed between concurrent turns.

Pure/in-process — no DB, no network, no API keys.
"""
import asyncio

from lib.core import (
    Ctx,
    GroupScope,
    group_scope_for,
    invalidate_group_scope,
    app_context,
    bind_app_context,
    reset_app_context,
)


def test_group_scope_delegation():
    gs = GroupScope()
    ctx = Ctx(group_scope=gs, user_id="u1", group_id="g1")
    # Tool-facing attributes delegate to the group scope.
    ctx.devices = {"lamp": object()}
    ctx.known_adresses = {"home": "x"}
    assert gs.devices == ctx.devices
    assert gs.known_adresses == ctx.known_adresses
    # Per-user/per-turn state stays on the Ctx.
    assert ctx.user_id == "u1" and ctx.group_id == "g1"


def test_group_scope_cache_shared_and_isolated(monkeypatch):
    monkeypatch.setenv("GROUP_SCOPE_TTL_SECONDS", "3600")
    invalidate_group_scope()
    a1 = group_scope_for("dom")
    a2 = group_scope_for("dom")
    b = group_scope_for("dzieci")
    assert a1 is a2           # same group → same cached scope
    assert a1 is not b        # different group → different scope
    a1.devices["lamp"] = 1
    assert "lamp" not in b.devices  # no bleed across groups
    invalidate_group_scope("dom")
    assert group_scope_for("dom") is not a1  # invalidation drops it


def test_group_scope_ttl_zero_is_uncached(monkeypatch):
    monkeypatch.setenv("GROUP_SCOPE_TTL_SECONDS", "0")
    assert group_scope_for("dom") is not group_scope_for("dom")


def test_no_cross_principal_bleed_between_concurrent_turns():
    """Two concurrent turns each bind their own principal Ctx; neither sees the other's."""
    seen: dict[str, str] = {}

    async def _turn(user_id: str, group_id: str) -> None:
        ctx = Ctx(group_scope=group_scope_for(group_id), user_id=user_id, group_id=group_id)
        token = bind_app_context(ctx)
        try:
            await asyncio.sleep(0.01)  # yield so the other turn interleaves
            app_context(ctx).devices[user_id] = True  # write group-scoped state
            await asyncio.sleep(0.01)
            seen[user_id] = app_context().user_id
        finally:
            reset_app_context(token)

    async def _main() -> None:
        invalidate_group_scope()
        await asyncio.gather(
            _turn("userA", "groupA"),
            _turn("userB", "groupB"),
        )

    asyncio.run(_main())
    # Each concurrent turn saw its own principal, not the other's.
    assert seen == {"userA": "userA", "userB": "userB"}
