"""fpl_agent feature package. Public surface: create_fpl_agent."""

from lib.agents.fpl_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.fpl_agent.agent import create_fpl_agent

__all__ = ["create_fpl_agent"]
