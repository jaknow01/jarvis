"""Helpers to give config-defined sub-agents a tool listing without any server."""

from contextlib import contextmanager

from lib.agents import MCP_SUBAGENTS, toolset_for
from lib.core import RemoteTool


def fake_tool(name: str, description: str = "A tool.", props: dict | None = None) -> RemoteTool:
    return RemoteTool(name=name, description=description,
                      input_schema={"type": "object", "properties": props or {}})


@contextmanager
def listed_subagents(names=None, call=None):
    """Install a one-tool snapshot on each named sub-agent's toolset (default: all)."""
    names = list(names or MCP_SUBAGENTS)
    saved = {}
    for name in names:
        ts = toolset_for(name)
        saved[name] = (ts._remote, ts._tools, ts.instructions, ts._loaded_at)
        ts.load_snapshot([fake_tool(f"{name}_tool")], f"Guide for {name}.", call=call)
    try:
        yield
    finally:
        for name, (remote, tools, instructions, loaded_at) in saved.items():
            ts = toolset_for(name)
            ts._remote, ts._tools, ts.instructions, ts._loaded_at = remote, tools, instructions, loaded_at
