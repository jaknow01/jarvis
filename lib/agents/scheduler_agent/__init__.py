"""scheduler_agent feature package. Public surface: create_scheduler_agent."""

from lib.agents.scheduler_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.scheduler_agent.agent import create_scheduler_agent

__all__ = ["create_scheduler_agent"]
