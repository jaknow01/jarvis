import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator, env_str, register_mcp_toolset

logger = logging.getLogger(__name__)


def fpl_tools_source() -> str:
    """``FPL_TOOLS_SOURCE``: ``local`` (default, in-process tools) or ``mcp`` (the FPL MCP
    server at ``FPL_MCP_URL``, authenticated with ``FPL_MCP_API_KEY``)."""
    return (env_str("FPL_TOOLS_SOURCE", "local") or "local").lower()


# The remote twin of TOOLS_BY_AGENT["fpl_agent"]. The engine lists it before each turn
# (refresh_mcp_toolsets) only while FPL_TOOLS_SOURCE=mcp. `whoami` is for people's
# clients (Claude apps), not for this agent.
FPL_MCP = register_mcp_toolset(
    "fpl", url_env="FPL_MCP_URL", key_env="FPL_MCP_API_KEY",
    enabled=lambda: fpl_tools_source() == "mcp", exclude=frozenset({"whoami"}),
)


def _fpl_tools(name: str) -> list:
    if fpl_tools_source() == "mcp":
        remote = FPL_MCP.tools()
        if remote:
            return remote
        # Server unreachable or misconfigured: keep the agent working on the in-process
        # tools rather than leaving it with nothing to call.
        logger.warning("FPL tools: MCP source unavailable (%s) — falling back to local tools",
                       FPL_MCP.last_error or "not loaded yet")
    return TOOLS_BY_AGENT[name]


@agents_decorator(name="fpl_agent")
def create_fpl_agent() -> FunctionAgent:
    name = "fpl_agent"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Fantasy Premier League: upcoming fixtures, PL teams, the owner's squad and mini-league standings.",
        system_prompt=(
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
            - get_fpl_player: look up ANY single player by name - current price, position, team,\
              total points, form, ownership%, availability status and their next fixtures with FDR.\
              Use it for 'ile kosztuje Haaland', 'jaka forma Saki', 'z kim gra teraz Palmer'. If it\
              returns ambiguous:true (several name matches), ask the user which player before retrying.\
              Its status/chance_of_playing is AVAILABILITY (injury/suspension), NOT a confirmed lineup.\
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
        tools=_fpl_tools(name),
        llm=llm,
    )

    logger.info("FPL agent created")
    return agent
