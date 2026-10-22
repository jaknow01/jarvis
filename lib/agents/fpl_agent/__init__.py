"""fpl_agent feature package. Public surface: create_fpl_agent + the caller-identity API
(the MCP server binds each API key's default manager/league ids with it)."""

from lib.agents.fpl_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.fpl_agent.agent import create_fpl_agent
from lib.agents.fpl_agent.identity import (
    FplIdentity,
    current_fpl_identity,
    using_fpl_identity,
)
from lib.agents.fpl_agent.fpl_api import list_league_managers

__all__ = ["create_fpl_agent", "FplIdentity", "current_fpl_identity", "using_fpl_identity",
           "list_league_managers"]
