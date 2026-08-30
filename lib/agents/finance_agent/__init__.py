"""finance_agent feature package. Public surface: create_finance_agent."""

from lib.agents.finance_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.finance_agent.tools import get_exchange_rate, get_stock_quote
from lib.agents.finance_agent.agent import create_finance_agent

# get_exchange_rate / get_stock_quote are re-exported so the watch source registry
# (lib/agents/scheduler_agent/watch_sources.py) can reuse them via the package surface.
__all__ = ["create_finance_agent", "get_exchange_rate", "get_stock_quote"]
