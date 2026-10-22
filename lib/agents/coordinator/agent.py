import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, agents_decorator, agent_enabled, agent_as_tool, env_flag
from lib.domain import memory
from lib.domain.profile import Profile

# Harness sub-agents live in this repo; tool sub-agents come from config/subagents.yaml and
# are backed by MCP servers (lib/agents/mcp_subagents). These packages never import the
# coordinator, so there is no import cycle.
from lib.agents.memory_operator import create_memory_agent
from lib.agents.scheduler_agent import create_scheduler_agent
from lib.agents.mcp_subagents import mcp_subagent_catalog

from lib.agents.coordinator.no_data import TOOL_NAME as NO_DATA_TOOL, no_data_tool

logger = logging.getLogger(__name__)


# Canonical catalog of subagents (name, factory, tool description). The coordinator's
# roster for a turn is the subset enabled by the acting principal's resolved profile
# (see lib/domain/profile.py); with no profile it falls back to the global env toggles.
# A tool sub-agent whose MCP server has never been listed is left out of the turn.
def subagent_catalog() -> list[tuple]:
    return [
        *mcp_subagent_catalog(),
        ("memory_operator", create_memory_agent, "Stores, retrieves and updates the user's long-term preferences and facts."),
        ("scheduler_agent", create_scheduler_agent, "Schedules Jarvis to act on its own later: one-off reminders, recurring proactive briefs, AND conditional watches that poll live data and notify ONLY when a condition becomes true ('daj znać, gdy...', 'powiadom, kiedy...'); plus listing/cancelling them."),
    ]


def force_tool_choice() -> bool:
    """``COORDINATOR_FORCE_TOOL`` (default on): force the coordinator's first LLM step to
    call a tool (``initial_tool_choice="required"``), so it delegates instead of answering
    from the conversation history. Weaker/local models otherwise skip delegation on
    follow-up questions and invent figures. Adds the ``no_data_needed`` escape tool."""
    return env_flag("COORDINATOR_FORCE_TOOL", True)


def subagent_names() -> list[str]:
    """Candidate subagent names for profile resolution (the resolver lives in `domain`
    and must not import this `agents` module, so the caller supplies these)."""
    return [n for n, _, _ in subagent_catalog()]


@agents_decorator(name="coordinator")
def create_coordinator_agent(profile: Profile | None = None) -> FunctionAgent:
    name = "coordinator"
    llm = LLM_BY_AGENT[name]()

    # A turn passes the acting principal's resolved profile; without one (legacy callers,
    # some tests) fall back to the global env toggles so behavior is unchanged.
    def is_on(agent_name: str) -> bool:
        return profile.is_enabled(agent_name) if profile is not None else agent_enabled(agent_name)

    user_id = profile.user_id if profile is not None else None

    instructions = (
        "You are a coordinator of a multiagent personal assistant network called Jarvis.\
        Your job is to gather the data needed to satisfy the user's request by delegating to specialized subagents.\
        In your possesion there are numerous specialized subagents which you can call as your tool\
        Each agent specializes in a narrow field that is of interest to the user.\
        These agents are equipped with various API connectors that allow them to obtain relevant, real-time data or perform certain actions\
        You should always call appropriate agent instead of relying on your built in knowledge.\
        Your tool-subagents can be run in parallel if the query requires multidomain knowledge.\
        You can also run the same tool-subagent multiple times in parallel if the query justifies it - it is especially helpful with news-agent.\
        \
        IMPORTANT: when you are done gathering, YOU write the answer to the user's message, in Polish. You own its\
        entire content: which facts and numbers it contains, what could not be obtained and why, what was done.\
        A separate formatting step only polishes the wording and layout of your text for a chat on a phone; it\
        adds nothing, removes nothing and fixes no mistakes - so your answer must be complete and correct as is.\
        Answer content rules:\
        - Use only facts, numbers, units and dates returned by subagents in THIS turn, exactly as returned.\
        - Answer every part of the user's message. If a part could not be obtained (a subagent failed, timed\
          out or returned something unusable), say plainly that this part is unavailable right now, without\
          technical jargon, instead of filling it in.\
        - Report an action (reminder, lights, memory, calendar) as done only if the subagent confirmed it.\
        - Speak as a single assistant: do not mention subagents, tools or how the data was gathered."
    )

    instructions += (
        "\n\nMandatory delegation rules:\n"
        "- EVERY user message that asks for information or an action MUST be answered by calling at "
        "least one subagent in THIS turn. This applies to follow-up questions too ('a funt?', 'a jutro?', "
        "'a tam?'): resolve what they refer to from the conversation, then call the subagent again.\n"
        "- Never answer from the conversation history, the running summary, the user's long-term memory "
        "or your own knowledge. Earlier values are stale: exchange rates, quotes and indices, weather, "
        "news, routes, device states, FPL data, calendar events and scheduled jobs must be fetched fresh "
        "every turn, even if the same value appeared a moment ago.\n"
        "- Actions (reminders, lights, saving to memory, calendar changes) happen ONLY through the "
        "matching subagent.\n"
        "- Never write numbers, prices or facts into your answer that no subagent returned in this turn.\n"
        "- Long-term memory tells you HOW to personalize (preferences, saved places); it is not a "
        "source of current data.\n"
        "- If a subagent's result is clearly unrelated to what you asked (wrong currency, ticker, place "
        "or date), you may call it once more with a more precise request. If it is still wrong, do not "
        "loop: report that this part is unavailable.\n"
        "- If no subagent can handle the request, say so plainly in your answer rather than answering from your own knowledge."
    )

    # The roster (which subagents are exposed as tools) is decided per principal by the
    # resolved profile; a disabled agent is simply not exposed as a tool.
    subagents = subagent_catalog()

    if is_on("scheduler_agent"):
        instructions += (
            "\n\nWhen the user wants something to happen later or on its own, delegate to the "
            "scheduler_agent. There are two shapes and you MUST NOT collapse the second into the first:\n"
            "- TIME-BASED ('przypomnij mi za...', 'codziennie o 8', 'co rano dawaj mi...'): a "
            "reminder/brief that fires on a clock. It will be re-run by you automatically, so phrase "
            "the job's prompt as a complete, self-contained request.\n"
            "- CONDITIONAL ('daj znać, GDY/KIEDY...', 'powiadom, gdy...', 'tylko jeśli...', 'sprawdzaj "
            "co minutę, czy...'): a watch that polls live data and notifies ONLY when a condition "
            "becomes true. Forward the user's request FAITHFULLY to the scheduler_agent — preserve the "
            "trigger condition, the 'only when' wording AND any stop/end condition (e.g. 'do końca "
            "meczu'). Do NOT rewrite it into a 'do X co minutę' procedure and do NOT ask for a frequent "
            "cron: turning a conditional alert into a per-minute cron floods the user and is a bug. Let "
            "the scheduler_agent pick the mechanism; for a watch it will confirm the condition with the "
            "user BEFORE arming, so pass its confirmation question/description straight back — do not "
            "report the watch as already set up until the user has confirmed.\n"
            "- CANCELLING ('przestań mnie powiadamiać', 'wyłącz/odwołaj/usuń to przypomnienie lub "
            "watch', 'nie wysyłaj mi już...'): ALSO delegate to the scheduler_agent — stopping a "
            "conditional watch goes through it exactly like cancelling a reminder; do NOT handle it "
            "yourself. Relay its ACTUAL result: only tell the user something was cancelled if the "
            "scheduler_agent confirms it was deleted; if it could not find/cancel it, say so (with "
            "what is still active) instead of claiming success."
        )

    if is_on("memory_operator"):
        instructions += (
            "\n\nWhen the user states a durable preference or fact about themselves, store it via the "
            "memory_operator so future conversations stay personalized. Consult the memory_operator when "
            "you need details about the user's saved preferences."
        )
        mem_profile = memory.summary(user_id=user_id)
        if mem_profile:
            instructions += (
                "\n\nWhat you already know about the user (long-term memory — use it to personalize "
                "and to fill in details instead of asking the user again; it never replaces calling "
                "the subagents for current data or actions):\n" + mem_profile
            )

    tools = []
    for sub_name, factory, description in subagents:
        if not is_on(sub_name):
            logger.info(f"Subagent '{sub_name}' not in roster; not exposed to coordinator")
            continue
        agent = factory()
        if agent is None:  # its MCP server has not been reachable yet
            logger.warning(f"Subagent '{sub_name}' unavailable (tool server not listed); left out this turn")
            continue
        tools.append(agent_as_tool(agent, tool_name=sub_name, tool_description=description))

    force = force_tool_choice()
    if force:
        tools.append(no_data_tool())
        instructions += (
            f"\n\nYou must call at least one tool on every message. If, and only if, the message is pure "
            f"conversation that needs no data and no action (greeting, thanks, small talk, a bare 'ok', "
            f"farewell), call `{NO_DATA_TOOL}` instead of a subagent. Never use it for a question, a "
            f"follow-up or a request — those always go to a subagent."
        )

    agent = FunctionAgent(
        name=name,
        description="Routes user requests to specialized subagents and writes the answer from their results.",
        system_prompt=instructions,
        tools=tools,
        llm=llm,
        allow_parallel_tool_calls=True,
        initial_tool_choice="required" if force else None,
    )
    logger.info(f"Coordinator initiated with {len(tools)} subagent tool(s)")

    return agent
