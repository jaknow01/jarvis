"""math_agent feature package. Public surface: create_math_agent."""

from lib.agents.math_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.math_agent.agent import create_math_agent

__all__ = ["create_math_agent"]
