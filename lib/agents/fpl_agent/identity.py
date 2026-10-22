"""Who "my squad" / "my league" refers to in the FPL tools.

The tools resolve the manager ("entry") id and default league id in this order:

1. an explicit ``entry_id`` / ``league_id`` argument on the tool call;
2. the identity bound for the current call with :func:`using_fpl_identity` — the MCP
   server binds the defaults linked to the caller's API key, so each friend's
   "my team" is their own team;
3. the ``FPL_ENTRY_ID`` / ``FPL_LEAGUE_ID`` env vars — only when NO identity is bound
   (the in-process Jarvis path).

A bound identity is authoritative: a missing id in it stays missing and never falls back
to the env, so a caller of the shared MCP server can never get the owner's team by
accident.
"""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class FplIdentity:
    """Default FPL ids for one caller (e.g. one MCP API key)."""

    entry_id: Optional[int] = None
    league_id: Optional[int] = None


_IDENTITY: ContextVar[Optional[FplIdentity]] = ContextVar("fpl_identity", default=None)


@contextmanager
def using_fpl_identity(identity: FplIdentity) -> Iterator[None]:
    """Bind ``identity`` as the defaults for FPL tool calls inside the block."""
    token = _IDENTITY.set(identity)
    try:
        yield
    finally:
        _IDENTITY.reset(token)


def current_fpl_identity() -> Optional[FplIdentity]:
    return _IDENTITY.get()


def _env_id(name: str) -> Optional[int]:
    raw = os.getenv(name)
    return int(raw) if raw and raw.strip().isdigit() else None


def resolve_entry_id(override: Optional[int]) -> Optional[int]:
    if override is not None:
        return int(override)
    identity = _IDENTITY.get()
    if identity is not None:
        return identity.entry_id
    return _env_id("FPL_ENTRY_ID")


def resolve_league_id(override: Optional[int]) -> Optional[int]:
    if override is not None:
        return int(override)
    identity = _IDENTITY.get()
    if identity is not None:
        return identity.league_id
    return _env_id("FPL_LEAGUE_ID")


def missing_entry_tip() -> str:
    if _IDENTITY.get() is not None:
        return ("No default FPL manager id is linked to this caller. Pass entry_id explicitly "
                "(the number in the FPL site URL: fantasy.premierleague.com/entry/<id>/...), "
                "or ask the server admin to link one to your API key.")
    return "Set FPL_ENTRY_ID in the environment or pass entry_id explicitly."


def missing_league_tip() -> str:
    if _IDENTITY.get() is not None:
        return ("No default league is linked to this caller. Pass league_id explicitly, or "
                "call get_my_fpl_leagues to find your league ids.")
    return ("Set FPL_LEAGUE_ID, pass league_id, or call get_my_fpl_leagues to find your "
            "league ids.")
