"""calendar_agent feature package. Public surface: create_calendar_agent.

Manages the owner's Apple iCloud calendar over CalDAV (list/create/update/delete events).
Credentials live in the environment and never reach the model — see caldav_link.py.
"""

# Import tools for their registration side effect (populates TOOLS_BY_AGENT).
from lib.agents.calendar_agent import tools as tools  # noqa: F401
from lib.agents.calendar_agent.agent import create_calendar_agent

__all__ = ["create_calendar_agent"]
