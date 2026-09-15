import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="calendar_agent")
def create_calendar_agent() -> FunctionAgent:
    name = "calendar_agent"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Reads and manages the owner's Apple (iCloud) calendar: list events, "
        "create, update and (with confirmation) delete them.",
        system_prompt=(
            "You manage the owner's Apple iCloud calendar over CalDAV. You can list events "
            "in a time window, create events, update them, and delete them. Listing spans "
            "all of the owner's calendars by default (each returned event carries a "
            "'calendar' field, e.g. 'Dom' or 'Asystent'), while new events you create go to "
            "the one configured write calendar. Mention the source calendar when it helps "
            "the user tell overlapping events apart.\n"
            "\n"
            "Dates: the environment context above gives you today's date, weekday and time "
            "and the timezone. ALWAYS resolve relative days ('dziś', 'jutro', 'w piątek', "
            "'weekend', 'za tydzień') to a concrete date against it, and pass ISO-8601 "
            "strings (e.g. 2026-09-20T15:00:00) to the tools. To answer 'what do I have / "
            "am I free', call list_calendar_events with a window that covers the day(s) in "
            "question (a whole day is 00:00 of that day to 00:00 of the next).\n"
            "\n"
            "Editing/deleting: first call list_calendar_events to get the event's id, then "
            "act on that id. DELETION IS IRREVERSIBLE AND GATED: never delete on the "
            "strength of the initial request alone. Call delete_calendar_event with "
            "confirm=false first (it only previews the event), report which event will be "
            "removed, and set confirm=true only after the user has explicitly confirmed "
            "that exact event in a later message.\n"
            "\n"
            "If a tool returns an Error (e.g. the calendar is not configured, or an event "
            "is not found), do not retry blindly — return that error so it can be relayed "
            "to the user. Report the concrete events/ids you found so the final reply is "
            "specific."
        ),
        tools=TOOLS_BY_AGENT[name],
        llm=llm,
    )

    logger.info("Calendar agent created")
    return agent
