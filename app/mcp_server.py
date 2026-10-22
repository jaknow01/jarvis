"""Public MCP tool servers — HTTP entry point (see docs/MCP.md).

    poetry run python -m app.mcp_server            # 0.0.0.0:8010, all exposable servers

Env: MCP_HOST / MCP_PORT (default 0.0.0.0 / 8010), MCP_SERVERS (comma list, default all
exposable — today just `fpl`), MCP_CLIENTS_CONFIG (default config/mcp_clients.yaml),
MCP_RATE_LIMIT_PER_MINUTE (default 60), MCP_TOOL_TIMEOUT_SECONDS (default 90).

Access logging is OFF on purpose: with the key-in-URL form (`/mcp/fpl/<key>`) an access
log would write every client's API key to disk. The app logs each call by client id.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from lib.core import Logger  # noqa: E402
from lib.runtime import build_mcp_app  # noqa: E402

Logger.config_root_logger()

_servers = [s.strip() for s in os.getenv("MCP_SERVERS", "").split(",") if s.strip()] or None
app = build_mcp_app(_servers)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", "8010")),
                access_log=False, proxy_headers=True)


if __name__ == "__main__":
    main()
