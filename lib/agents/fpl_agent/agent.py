import logging

from agents import Agent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


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
