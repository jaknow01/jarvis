"""Interim "still working" progress ping for long turns.

When a coordinator turn takes longer than ``PROGRESS_PING_AFTER_SECONDS`` the engine
pushes **one** templated Polish message so the user knows Jarvis is alive rather than
hung — enriched with which subagents have already returned, read off the coordinator's
event stream (``ToolCallResult`` events). There is deliberately **no LLM call**: the
message is assembled from a static label map, so it never adds latency to the very
thing it is meant to reassure about.

The engine owns the timing/streaming (see ``lib/runtime/engine.py``); this module is
the pure presentation + config layer, kept separate so it is trivially unit-testable.
"""

import os

# Subagent tool name (the coordinator's tool names — see lib/agents/coordinator/agent.py)
# -> friendly Polish noun phrase to slot into "sprawdziłem już ...". Unknown tool names
# are simply omitted from the note (the ping still fires, just without that item).
_LABELS: dict[str, str] = {
    "weather_agent": "pogodę",
    "maps_agent": "trasę",
    "finance_agent": "dane finansowe",
    "news_agent": "newsy",
    "fpl_agent": "dane FPL",
    "iot_operator": "urządzenia",
    "memory_operator": "pamięć",
    "scheduler_agent": "harmonogram",
}

_FALSY = {"0", "false", "no", "off", "disabled"}


def label_for(tool_name: str) -> str | None:
    """Friendly Polish label for a subagent tool name, or None if unmapped."""
    return _LABELS.get(tool_name)


def pl_join(items: list[str]) -> str:
    """Join label phrases the Polish way: 'a' / 'a i b' / 'a, b i c'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " i " + items[-1]


def progress_message(done: list[str]) -> str:
    """Build the interim ping text.

    Args:
        done: friendly labels (via ``label_for``) of the subagents that have already
            returned by the moment the ping fires. May be empty.
    """
    if done:
        return (
            "Chwila, jeszcze nad tym pracuję — sprawdziłem już "
            + pl_join(done)
            + ". Zaraz kończę…"
        )
    return "Chwila, jeszcze nad tym pracuję…"


def ping_enabled() -> bool:
    """Master switch, ``PROGRESS_PING_ENABLED`` (default on; falsy-parsed like the
    other env toggles: 0/false/no/off/disabled/empty)."""
    raw = os.getenv("PROGRESS_PING_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in _FALSY


def ping_after_seconds() -> float:
    """Delay before the interim ping fires, ``PROGRESS_PING_AFTER_SECONDS`` (default 7)."""
    raw = os.getenv("PROGRESS_PING_AFTER_SECONDS")
    if raw is None or raw.strip() == "":
        return 7.0
    try:
        return float(raw)
    except ValueError:
        return 7.0
