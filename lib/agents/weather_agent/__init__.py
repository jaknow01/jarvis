"""weather_agent feature package. Public surface: create_weather_agent."""

# Import tools for their registration side effect (populates TOOLS_BY_AGENT).
from lib.agents.weather_agent import tools as tools  # noqa: F401
from lib.agents.weather_agent.agent import create_weather_agent

__all__ = ["create_weather_agent"]
