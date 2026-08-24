"""maps_agent feature package. Public surface: create_maps_agent."""

from lib.agents.maps_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.maps_agent.agent import create_maps_agent

__all__ = ["create_maps_agent"]
