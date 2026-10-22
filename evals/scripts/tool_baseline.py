"""Record the in-process tool catalogue as the parity baseline for the MCP extraction.

    poetry run python -m evals.scripts.tool_baseline            # → evals/fixtures/tool_baseline.json

For every domain that moves to its own MCP server (openspec change
extract-tools-to-mcp-servers, decision D3) it writes each tool's name, description and
input schema (without the hidden LlamaIndex `ctx`). The servers are later checked against
this file: same tool names, equivalent input schemas.
"""

import json
from pathlib import Path

import lib.agents  # noqa: F401  (registration side effect: fills TOOLS_BY_AGENT)
from lib.core import TOOLS_BY_AGENT

# Jarvis agent name → future server repo. memory_operator / scheduler_agent stay in Jarvis.
EXTRACTED = {
    "fpl_agent": "mcp-fpl",
    "finance_agent": "mcp-finance",
    "weather_agent": "mcp-weather",
    "news_agent": "mcp-news",
    "maps_agent": "mcp-maps",
    "calendar_agent": "mcp-calendar",
    "iot_operator": "mcp-home",
    "web_agent": "mcp-web",
    "math_agent": "mcp-math",
}

OUT = Path("evals/fixtures/tool_baseline.json")


def build() -> dict:
    servers = {}
    for agent, repo in EXTRACTED.items():
        tools = TOOLS_BY_AGENT.get(agent)
        if not tools:
            servers[repo] = {"agent": agent, "missing": True, "tools": []}
            continue
        servers[repo] = {
            "agent": agent,
            "tools": [
                {
                    "name": t.metadata.name,
                    "description": t.metadata.description,
                    "input_schema": t.metadata.get_parameters_dict(),
                }
                for t in sorted(tools, key=lambda t: t.metadata.name)
            ],
        }
    return {"servers": servers}


def main() -> None:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for repo, entry in data["servers"].items():
        state = "MISSING (agent not on this branch)" if entry.get("missing") else f"{len(entry['tools'])} tools"
        print(f"{repo:<14} {state}")


if __name__ == "__main__":
    main()
