"""Refresh the MCP tool-schema snapshot evals run against (no servers needed at eval time).

    poetry run python -m evals.snapshot_tools          # needs MCP_<SERVER>_URL / _KEY in env

Lists every sub-agent in config/subagents.yaml from its live server and rewrites
evals/fixtures/mcp_tools.json (tools without whoami/admin tools, plus each server's
`instructions`). Servers that cannot be reached keep their previous snapshot entry.
"""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

SNAPSHOT = Path("evals/fixtures/mcp_tools.json")


async def _refresh() -> dict:
    from lib.agents import MCP_SUBAGENTS, toolset_for

    old = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["servers"] if SNAPSHOT.exists() else {}
    servers = dict(old)
    for name in MCP_SUBAGENTS:
        ts = toolset_for(name)
        await ts.refresh(force=True)
        if ts.remote_tools() is None:
            print(f"{name:<15} unreachable ({ts.last_error}) — keeping previous snapshot")
            continue
        servers[name] = {"instructions": ts.instructions,
                         "tools": [asdict(t) for t in ts.remote_tools()]}
        print(f"{name:<15} {len(servers[name]['tools'])} tools")
    return {"servers": servers}


def main() -> None:
    load_dotenv()
    data = asyncio.run(_refresh())
    SNAPSHOT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
