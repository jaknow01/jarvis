"""Environment / context provider for the agent network.

A single source of truth for "environment" facts that every agent should be aware
of (current date/time, timezone, base currency, locale). Injected into each agent's
instructions at build time so temporal/locale awareness reaches all agents without an
extra tool hop — not just the weather agent that owns get_current_date_and_time.

No geolocation: the service has no reliable way to know the user's physical location,
so location is intentionally omitted (see docs/POTENTIAL_AGENTS.md).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

# Owner defaults (personal assistant, single user — see CLAUDE.md).
TIMEZONE = "Europe/Warsaw"
BASE_CURRENCY = "PLN"
LOCALE = "pl-PL"


def environment_preamble() -> str:
    """Build the environment-context block prepended to every agent's instructions.

    The dynamic parts (date/time/weekday) are evaluated fresh on each call; agents are
    rebuilt every turn, so the embedded timestamp stays current.
    """
    now = datetime.now(ZoneInfo(TIMEZONE))
    return (
        "Environment context (single source of truth — use it to interpret relative "
        "dates/times and to pick locale/currency defaults):\n"
        f"- Current date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})\n"
        f"- Current time: {now.strftime('%H:%M')} ({TIMEZONE})\n"
        f"- Base currency: {BASE_CURRENCY}\n"
        f"- Locale: {LOCALE}\n\n"
    )
