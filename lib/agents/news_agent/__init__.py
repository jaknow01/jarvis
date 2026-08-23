"""news_agent feature package. Public surface: create_news_agent."""

from lib.agents.news_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.news_agent.agent import create_news_agent

__all__ = ["create_news_agent"]
