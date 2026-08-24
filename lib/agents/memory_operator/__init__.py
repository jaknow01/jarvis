"""memory_operator feature package. Public surface: create_memory_agent."""

from lib.agents.memory_operator import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.memory_operator.agent import create_memory_agent

__all__ = ["create_memory_agent"]
