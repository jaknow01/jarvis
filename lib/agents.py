from lib.tools import (
    get_devices_state,
    turn_on_devices,
    get_route_details,
    get_maps_memory,
    get_exchange_rate)
from lib.llm import LLM_BY_AGENT
from lib.tools import TOOLS_BY_AGENT
from lib.memory import memory
from lib.context import environment_preamble
from agents import Agent
import logging
import os

logger = logging.getLogger(__name__)
AGENTS: dict = {}

def agents_decorator(name: str):
    def wrapper(func):
        def build(*args, **kwargs):
            agent = func(*args, **kwargs)
            # Context provider: prepend the shared environment block (date/time, timezone,
            # currency, locale) to every agent's instructions at build time, so temporal
            # and locale awareness reaches all agents — not just the one owning the
            # date/time tool. Callables-as-instructions are left untouched.
            if isinstance(agent.instructions, str):
                agent.instructions = environment_preamble() + agent.instructions
            return agent
        AGENTS[name] = build
        return build
    return wrapper

def agent_enabled(name: str) -> bool:
    """Whether a subagent is exposed to the coordinator. Controlled by the env var
    AGENT_<NAME>_ENABLED — enabled by default; set to a falsy value (0/false/no/off)
    to hide it as a coordinator tool. Read at coordinator-build time."""
    raw = os.getenv(f"AGENT_{name.upper()}_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")

@agents_decorator(name="coordinator")
def create_coordinator_agent() -> Agent:
    name = "coordinator"
    model_settings = LLM_BY_AGENT[name]()

    # create_subagents() - trzeba stworzyc pomocnikow

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
        IMPORTANT: You do NOT write the final answer to the user yourself. Once you have gathered everything needed\
        (including any error messages from failed tool calls), you MUST always hand off to the 'composer' agent, which\
        writes the final user-facing reply. Do not paraphrase or summarize the results yourself - just gather and hand off.\
        If a tool call fails, still hand off to the composer and let it inform the user; pass along what went wrong."
    )

    # Each subagent can be switched off via AGENT_<NAME>_ENABLED; a disabled agent is
    # simply not exposed to the coordinator as a tool.
    subagents = [
        ("iot_operator", create_iot_agent, "Controls smart devices (lighting) in a houshold."),
        ("weather_agent", create_weather_agent, "Checks current weather and weather forecast at a given location"),
        ("finance_agent", create_finance_agent, "Retrieves and analyzes financial data."),
        ("maps_agent", create_maps_agent, "Controls access to maps and navigation. Can calculate routes."),
        ("news_agent", create_news_agent, "Summarizes current world and financial-market news."),
        ("memory_operator", create_memory_agent, "Stores, retrieves and updates the user's long-term preferences and facts."),
        ("fpl_agent", create_fpl_agent, "Fantasy Premier League: upcoming fixtures, PL teams, the owner's squad and mini-league standings."),
        ("scheduler_agent", create_scheduler_agent, "Schedules Jarvis to act on its own later: one-off reminders and recurring proactive briefs, plus listing/cancelling them."),
    ]

    if agent_enabled("scheduler_agent"):
        instructions += (
            "\n\nWhen the user asks to be reminded later or wants something done on a recurring "
            "schedule ('przypomnij mi za...', 'codziennie o...', 'co rano dawaj mi...'), delegate to "
            "the scheduler_agent to create/list/cancel scheduled jobs. The scheduled task itself will "
            "later be run by you again automatically, so phrase the job's prompt as a complete request."
        )

    if agent_enabled("memory_operator"):
        instructions += (
            "\n\nWhen the user states a durable preference or fact about themselves, store it via the "
            "memory_operator so future conversations stay personalized. Consult the memory_operator when "
            "you need details about the user's saved preferences."
        )
        profile = memory.summary()
        if profile:
            instructions += (
                "\n\nWhat you already know about the user (long-term memory — use it to personalize, "
                "and prefer it over asking the user again):\n" + profile
            )

    tools = []
    for sub_name, factory, description in subagents:
        if agent_enabled(sub_name):
            tools.append(factory().as_tool(tool_name=sub_name, tool_description=description))
        else:
            logger.info(f"Subagent '{sub_name}' disabled via env; not exposed to coordinator")

    agent = Agent(
        name = name,
        instructions = instructions,
        tools = tools,
        handoffs = [create_composer_agent()],
        model = model_settings["model_name"],
        model_settings = model_settings["settings"]
    )
    logger.info(f"Coordinator initiated with {len(tools)} subagent tool(s)")

    return agent

@agents_decorator(name="composer")
def create_composer_agent():
    name = "composer"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions=(
            "You are the response composer for the Jarvis assistant network. The coordinator has already\
            gathered all the data by delegating to specialized subagents, and then handed off to you.\
            Your job is to write the final, user-facing reply IN POLISH, based only on the data present in the\
            conversation so far.\
            Rules:\
            - Reply in Polish, clearly and concisely, in a natural assistant tone.\
            - Use only the information gathered by the subagents; never invent facts, numbers or data.\
            - Preserve numbers, units, dates and currency exactly as gathered (base currency is PLN).\
            - If a subagent reported an error or could not complete its task, tell the user plainly and simply\
              what went wrong, without technical jargon.\
            - Do not mention the internal agents, tools or the handoff mechanism - speak as a single assistant."
        ),
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Composer agent created")
    return agent

@agents_decorator(name="iot_operator")
def create_iot_agent():
    name = "iot_operator"

    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name = name,
        instructions=(
            "You are an operator of all smart devices (lighting) in the household.\
            Your task is to manipulate devices' states based on the user's preferences. \
            You must start your tool run by utilizing get_devices_state in order to initially access\
            the device database and establish connection, as well as to understand user's preferences\
            that are stored in long term memory database." \
            "Always try to run as many necessary tools as possible in paralel.\
            \
            ROOM- AND ZONE-SCOPED REQUESTS. Every device returned by get_devices_state carries a 'room'\
            field (e.g. 'living_room', 'bedroom') and a 'zones' list (e.g. ['entertainment_zone',\
            'work_zone']). Membership in a room or zone is defined ONLY by these fields - never infer it\
            from the device's name (a 'Telewizor' or 'Pianino' is in the living room if its 'room' says\
            so). Map the user's (Polish) words to the canonical values:\
            rooms: 'salon'/'pokój dzienny' -> living_room, 'sypialnia' -> bedroom, 'kuchnia' -> kitchen,\
            'łazienka' -> bathroom, 'gabinet'/'biuro' -> office, 'przedpokój'/'korytarz' -> hallway;\
            zones by meaning, e.g. 'strefa rozrywki'/'kino' -> entertainment_zone, 'strefa pracy' ->\
            work_zone, 'strefa spania' -> sleep_zone.\
            \
            COMPLETENESS IS MANDATORY. When the user targets a whole room, zone, or says 'wszystko'/'all',\
            follow this discipline every time:\
            (1) From the get_devices_state output, build the FULL list of devices whose 'room' (or 'zones')\
                matches the target, and count them.\
            (2) Issue the requested command to EVERY device on that list - do not stop after the first one\
                or two, and do not act only on the devices whose names sound relevant. Run them in parallel.\
            (3) Before finishing, verify that the number of devices you issued commands to equals the count\
                from step (1). If any matching device was left out, act on it now. Only then finish.\
            A room/zone request is complete only when every matching device has been handled.\
            \
            UNREACHABLE DEVICES. A device whose get_devices_state entry has an error/unreachable state\
            cannot be controlled. Do NOT silently drop it: still attempt the other devices in the room,\
            and clearly report which devices in the requested room could not be reached so the composer\
            can tell the user exactly what was and was not changed."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )
    logger.info("IoT agent initiated")

    return agent

@agents_decorator(name="maps_agent")
def create_maps_agent():
    name = "maps_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You are a Google Maps operator. Your task is to plan trips based on the traffic and the preferences\
            of the user.\
            You must always call get_maps_memory tool first before using any tools in order to understand user's preferences and be able \
            to understand user's requests in natural language. Without the output of this tool you may not be able to understand \
            user's requests."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Maps agent created")
    return agent
          
@agents_decorator(name="weather_agent")
def create_weather_agent():
    name="weather_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions=(
            "You can check current weather conditions as well as a short-term forecast.\
            Before answering ANY question that involves a date or a relative day\
            ('today', 'tomorrow', 'jutro', 'weekend', a weekday name, etc.), you MUST first call\
            get_current_date_and_time to establish today's date, weekday and time. Only then map the\
            user's relative day to a concrete calendar date and call weather_forecast. Never assume\
            what 'today' or 'tomorrow' is - always resolve it from get_current_date_and_time first,\
            and make sure the date you report back to the user matches that resolution."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Weather agent created")
    return agent

@agents_decorator(name="finance_agent")
def create_finance_agent():
    name = "finance_agent"

    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You are responsible for retrieving and analyzing financial data based on user's requests.\
            Make sure to use all necessary tools."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Finance agent created")
    return agent

@agents_decorator(name="memory_operator")
def create_memory_agent():
    name = "memory_operator"

    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You manage the user's long-term memory: durable preferences, facts, habits, interests and routines.\
            Use get_memory to look things up, save_memory to store a new durable preference or fact\
            (set source='user' when the user stated it explicitly, 'inferred' when you concluded it),\
            update_memory to correct an existing entry, and delete_memory to remove one.\
            Store concise, self-contained statements in natural language. Suggested categories:\
            preferences, facts, habits, interests, routines. Never store transient or one-off details,\
            secrets, or credentials."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Memory agent created")
    return agent

@agents_decorator(name="news_agent")
def create_news_agent():
    name = "news_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You are a news reporter. Your task is to search the internet and use reputable sources to create \
            summaries of the events mentioned by the user."),
        tools = TOOLS_BY_AGENT[name],
        model = model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("News agent created")
    return agent

@agents_decorator(name="fpl_agent")
def create_fpl_agent():
    name = "fpl_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions=(
            "You are a Fantasy Premier League (FPL) specialist. You answer questions about the\
            Premier League and the owner's own FPL team using real-time data from your tools -\
            never from built-in knowledge, which is out of date.\
            \
            Tool guide:\
            - get_fpl_fixtures: the schedule - matches for a gameweek with FDR difficulty\
              (1=easy..5=hard) and kickoff times. Defaults to the round currently in play, so\
              it answers 'najbliższe mecze'/'kiedy gramy'/'terminarz'. Use it for planning, not\
              for live scores.\
            - get_fpl_live: the real-time picture - which matches are being played RIGHT NOW,\
              their live score and minute, goals/assists/cards, and how the owner's own players\
              are doing live (minutes, goals, bonus, provisional points, whether their match is\
              in play). Use it for 'czy trwają jakieś mecze', 'jaki wynik', 'co się dzieje',\
              'jak grają moi zawodnicy'. If any_live is false, tell the user nothing is live now.\
              For each of the owner's players it also reports whether they actually took the pitch:\
              'started'/'playing_status' reflect the REAL match, and my_players.starters_not_playing\
              lists players the owner has in their FPL XI who are on the real bench / did not come on.\
              Proactively warn the user about those - they are fielding someone who is not playing.\
            - get_pl_teams: the Premier League teams and their strength ratings.\
            - get_my_fpl_squad: the owner's picked squad for a gameweek (defaults to the current one),\
              with captain/vice, bench, points, squad value and bank.\
            - get_my_fpl_leagues: the owner's mini-leagues and their ids - use it to resolve\
              'my league' when no league id is configured, then fetch its standings.\
            - get_fpl_league_standings: the table of a classic mini-league (defaults to the owner's\
              configured league); the owner's own row is flagged with is_me.\
            - who_owns_player_in_league: which managers in a mini-league own a given player, and how\
              many - for 'czy ktoś jeszcze ma Dalota', 'ile osób ma Haalanda'. If it returns\
              candidates (ambiguous name), ask the user which player they meant before retrying.\
            - get_league_ownership: the most-owned/most-captained players across a mini-league - for\
              'co popularne w mojej lidze', 'template'. Both scan real rivals' squads for the given\
              gameweek and default to the owner's configured league.\
            \
            The owner's manager id and default league come from configuration; if a tool reports that\
            none is configured, pass that message on plainly so the composer can tell the user how to\
            set it - do not invent an id. Run independent tools in parallel when a question spans\
            several of these (e.g. fixtures + squad). Report ids, points and prices exactly as returned."
        ),
        tools = TOOLS_BY_AGENT[name],
        model = model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("FPL agent created")
    return agent

@agents_decorator(name="scheduler_agent")
def create_scheduler_agent():
    name = "scheduler_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions=(
            "You manage the user's scheduled jobs: one-off reminders and recurring proactive\
            tasks that Jarvis runs on its own and then messages the user about.\
            \
            Tool guide:\
            - create_scheduled_job: schedule a task. Provide EXACTLY ONE of:\
              * delay_minutes - for relative one-offs ('za dwie godziny' -> 120, 'za pół godziny' -> 30);\
                prefer this for 'za X' so you never do date maths yourself.\
              * run_at (ISO date-time) - for an absolute one-off ('jutro o 9:00'); resolve the\
                concrete date from the environment date/time block first.\
              * cron_expr (5-field cron, Europe/Warsaw) - for anything recurring\
                ('codziennie o 8' -> '0 8 * * *', 'w dni robocze o 8' -> '0 8 * * 1-5',\
                'co godzinę' -> '0 * * * *'). Add `until` for 'przez miesiąc/tydzień'\
                (an ISO date a month/week ahead) and/or `max_runs` to cap repetitions.\
              The `prompt` you store is executed LATER by the whole assistant, so write it as a\
              complete, self-contained request in Polish (what to fetch and what to tell the user),\
              e.g. 'Podaj prognozę pogody na dziś w Warszawie, krótki przegląd najważniejszych\
              wiadomości, oraz sprawdź, czy dziś grają zawodnicy z mojego składu FPL.'\
            - list_scheduled_jobs: show active jobs (with ids) for the current conversation.\
            - delete_scheduled_job: cancel a job by id (find it via list first).\
            - update_scheduled_job: change a job's prompt or its until/max_runs limits; to change\
              the timing itself, delete and re-create.\
            \
            You do not deliver anything yourself - the runner delivers on the same channel the user\
            is on. Report back the created/updated job's id, human-readable schedule and next run so\
            the composer can confirm it to the user. If a tool returns an Error, pass it on plainly."
        ),
        tools = TOOLS_BY_AGENT[name],
        model = model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Scheduler agent created")
    return agent