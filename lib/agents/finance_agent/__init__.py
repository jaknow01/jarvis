"""finance_agent feature package. Public surface: create_finance_agent."""

from lib.agents.finance_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.finance_agent.agent import create_finance_agent

__all__ = ["create_finance_agent"]
