"""fpl_agent tools: Fantasy Premier League fixtures/FDR, PL teams, the owner's squad,
mini-league standings/ownership and live in-play data — all from the keyless public FPL
API. Registered into TOOLS_BY_AGENT["fpl_agent"] via @tool_ownership.

Numeric team/player/gameweek ids are resolved to human-readable names server-side (via
the bootstrap-static reference data in fpl_api), so the model — and the user — only ever
deal with names, not raw ids."""

import logging
import os
from typing import Optional

from llama_index.core.workflow import Context

from lib.core import tool_ownership, function_tool, app_context
from lib.agents.fpl_agent.fpl_api import (
    fetch_fpl,
    fetch_fpl_bootstrap,
    index_bootstrap,
    resolve_gameweek,
    relevant_gameweek,
    describe_player,
    summarize_fixture_events,
    find_players_by_name,
    load_league_squads,
    _LEAGUE_SCAN_DEFAULT,
)

logger = logging.getLogger(__name__)

# The owner's own manager ("entry") id and default league id are read from the env
# (FPL_ENTRY_ID / FPL_LEAGUE_ID), like the other API config. Each tool also accepts an
# explicit id override for ad-hoc lookups.

def _fpl_entry_id(override: Optional[int]) -> Optional[int]:
    if override is not None:
        return int(override)
    raw = os.getenv("FPL_ENTRY_ID")
    return int(raw) if raw and raw.strip().isdigit() else None


def _fpl_league_id(override: Optional[int]) -> Optional[int]:
    if override is not None:
        return int(override)
    raw = os.getenv("FPL_LEAGUE_ID")
    return int(raw) if raw and raw.strip().isdigit() else None


@tool_ownership("fpl_agent")
@function_tool
async def get_fpl_fixtures(ctx: Context, gameweek: Optional[int] = None) -> dict:
    """
    Description:
        Lists the Premier League fixtures (matches) for a Fantasy Premier League
        gameweek, with each team's official FDR difficulty rating (1 = easiest,
        5 = hardest) and kickoff time. Use this to answer "what are today's / the
        upcoming matches / this round" questions.

    Parameters:
    ctx : Context
        Context in which the tool operates

    gameweek: Optional[int] = None
        The gameweek number to list fixtures for. If omitted, the round currently in
        play is used: the current gameweek while it is still running (which is the one
        that contains "today's" matches), or the next gameweek once the current one has
        finished. To pinpoint a specific day, read each fixture's kickoff_time and
        compare it to today's date from the environment context.

    Output:
        JSON object with the resolved gameweek and a list of fixtures. Each fixture
        gives the home/away team names, their difficulty ratings, kickoff time, and —
        if the match has been played — the final score.
    """
    logger.info(f"Getting FPL fixtures for gameweek={gameweek or 'in-play'}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = gameweek if gameweek is not None else relevant_gameweek(bootstrap)
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, _, _ = index_bootstrap(bootstrap)
        fixtures = await fetch_fpl(f"fixtures/?event={gw}")
    except Exception as e:
        logger.error("Error while getting FPL fixtures", exc_info=True)
        return {"Message": "Error while getting FPL fixtures", "Error": str(e)}

    matches = []
    for f in fixtures:
        home = teams_by_id.get(f.get("team_h"), {})
        away = teams_by_id.get(f.get("team_a"), {})
        match = {
            "home": home.get("name"),
            "away": away.get("name"),
            "home_difficulty": f.get("team_h_difficulty"),
            "away_difficulty": f.get("team_a_difficulty"),
            "kickoff_time": f.get("kickoff_time"),
            "finished": f.get("finished"),
        }
        if f.get("finished") or f.get("started"):
            match["score"] = f"{f.get('team_h_score')}-{f.get('team_a_score')}"
        matches.append(match)

    return {"gameweek": gw, "fixtures_count": len(matches), "fixtures": matches}


@tool_ownership("fpl_agent")
@function_tool
async def get_pl_teams(ctx: Context) -> dict:
    """
    Description:
        Lists all Premier League teams in the current Fantasy Premier League season,
        with their short codes and FPL strength ratings (overall/attack/defence,
        split home/away, on a 1-5 scale). Use this for questions about the teams
        themselves (who is in the league, relative strength).

    Parameters:
    ctx : Context
        Context in which the tool operates

    Output:
        JSON object with a list of teams (name, short_name, and strength ratings).
    """
    logger.info("Getting Premier League teams")
    try:
        bootstrap = await fetch_fpl_bootstrap()
    except Exception as e:
        logger.error("Error while getting PL teams", exc_info=True)
        return {"Message": "Error while getting Premier League teams", "Error": str(e)}

    teams = [
        {
            "name": t.get("name"),
            "short_name": t.get("short_name"),
            "strength_overall_home": t.get("strength_overall_home"),
            "strength_overall_away": t.get("strength_overall_away"),
            "strength_attack_home": t.get("strength_attack_home"),
            "strength_attack_away": t.get("strength_attack_away"),
            "strength_defence_home": t.get("strength_defence_home"),
            "strength_defence_away": t.get("strength_defence_away"),
        }
        for t in bootstrap.get("teams", [])
    ]
    return {"teams_count": len(teams), "teams": teams}


@tool_ownership("fpl_agent")
@function_tool
async def get_fpl_player(ctx: Context, name: str, upcoming_fixtures: int = 5) -> dict:
    """
    Description:
        Looks up a single Premier League player by name and returns their FPL profile:
        current price, position, team, total points, form, ownership (% selected),
        availability status, and the next few fixtures with their FDR difficulty. Use
        this for questions about ANY player (not just the owner's squad), e.g. "how much
        does Haaland cost now?", "what's Saka's form?", "who does Palmer play next and
        how hard are the fixtures?".

    Parameters:
    ctx : Context
        Context in which the tool operates

    name: str
        Free-text player name to resolve (e.g. 'Haaland', 'Bruno Fernandes', 'Saka').
        Matching is by web name first (an exact web-name match wins); otherwise all
        substring matches across first/second/web name are returned for disambiguation.

    upcoming_fixtures: int = 5
        How many of the player's next fixtures to include (each with opponent,
        home/away, FDR difficulty 1-5 and kickoff time). Set to 0 to skip fixtures.

    Output:
        JSON object. On a single match: the player's profile plus `upcoming_fixtures`
        and `chance_of_playing_this_round`/`chance_of_playing_next_round`. When the name
        is ambiguous, returns `ambiguous: true` with a compact list of candidates (name,
        team, position, price) so the caller can pick one. Note: `status`/`chance_of_playing`
        report AVAILABILITY (injury/suspension), NOT a confirmed pre-match starting lineup —
        the FPL API does not publish announced XIs.
    """
    logger.info(f"Getting FPL player info for query={name!r}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        teams_by_id, _, positions_by_id = index_bootstrap(bootstrap)
        candidates = find_players_by_name(bootstrap, name)
    except Exception as e:
        logger.error("Error while getting FPL player", exc_info=True)
        return {"Message": "Error while getting the FPL player", "Error": str(e)}

    if not candidates:
        return {"Error": f"No Premier League player matches '{name}'.", "query": name}

    if len(candidates) > 1:
        return {
            "query": name,
            "ambiguous": True,
            "matches_count": len(candidates),
            "matches": [describe_player(e, teams_by_id, positions_by_id) for e in candidates[:15]],
            "Tip": "Several players matched; ask the user which one or pass a more specific name.",
        }

    element = candidates[0]
    info = describe_player(element, teams_by_id, positions_by_id)
    # Availability for the upcoming round(s) — an injury/suspension signal, NOT a
    # confirmed team sheet. The FPL API has no announced-lineup (starting XI vs bench) data.
    info["chance_of_playing_this_round"] = element.get("chance_of_playing_this_round")
    info["chance_of_playing_next_round"] = element.get("chance_of_playing_next_round")

    fixtures_out = []
    if upcoming_fixtures and upcoming_fixtures > 0:
        try:
            summary = await fetch_fpl(f"element-summary/{element.get('id')}/")
            for fx in (summary.get("fixtures") or [])[:upcoming_fixtures]:
                is_home = fx.get("is_home")
                opp = teams_by_id.get(fx.get("team_a") if is_home else fx.get("team_h"), {})
                fixtures_out.append({
                    "gameweek": fx.get("event"),
                    "opponent": opp.get("name"),
                    "opponent_short": opp.get("short_name"),
                    "home_or_away": "H" if is_home else "A",
                    "difficulty": fx.get("difficulty"),
                    "kickoff_time": fx.get("kickoff_time"),
                })
        except Exception:
            logger.warning("Could not fetch upcoming fixtures for player", exc_info=True)
    info["upcoming_fixtures"] = fixtures_out

    return info


@tool_ownership("fpl_agent")
@function_tool
async def get_my_fpl_squad(ctx: Context,
                           gameweek: Optional[int] = None,
                           entry_id: Optional[int] = None) -> dict:
    """
    Description:
        Returns the owner's Fantasy Premier League squad (the 15 players picked) for a
        gameweek, with each player resolved to name/team/position, the captain and
        vice-captain marked, and the starting XI vs. bench split. Also reports that
        gameweek's points, squad value and money in the bank.

    Parameters:
    ctx : Context
        Context in which the tool operates

    gameweek: Optional[int] = None
        The gameweek to fetch the squad for. If omitted, the current gameweek is used.
        Note: a squad only becomes public after that gameweek's deadline has passed.

    entry_id: Optional[int] = None
        The FPL manager ("entry") id to look up. If omitted, the owner's own id from
        the FPL_ENTRY_ID environment variable is used.

    Output:
        JSON object with the resolved gameweek, a per-gameweek summary (points, bank,
        squad value) and the list of picked players (starting XI first, then bench),
        each flagged with captaincy and whether they are on the bench.
    """
    eid = _fpl_entry_id(entry_id)
    if eid is None:
        return {
            "Error": "No FPL manager id is configured.",
            "Tip": "Set FPL_ENTRY_ID in the environment (your manager id, e.g. from the "
                   "URL fantasy.premierleague.com/entry/<ID>/event/1) or pass entry_id explicitly.",
        }
    logger.info(f"Getting FPL squad for entry={eid}, gameweek={gameweek or 'current'}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = resolve_gameweek(bootstrap, gameweek, prefer="current")
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, elements_by_id, positions_by_id = index_bootstrap(bootstrap)
        picks_data = await fetch_fpl(f"entry/{eid}/event/{gw}/picks/")
    except Exception as e:
        logger.error("Error while getting FPL squad", exc_info=True)
        return {
            "Message": "Error while getting the FPL squad",
            "Error": str(e),
            "Tip": "The squad is only public after that gameweek's deadline. Check the "
                   "manager id and that the gameweek has started.",
        }

    if isinstance(picks_data, dict) and picks_data.get("detail"):
        return {"Error": f"FPL API: {picks_data['detail']}", "entry_id": eid, "gameweek": gw}

    history = picks_data.get("entry_history", {}) or {}
    players = []
    for p in picks_data.get("picks", []):
        element = elements_by_id.get(p.get("element"), {})
        info = describe_player(element, teams_by_id, positions_by_id)
        # positions 1-11 are the starting XI, 12-15 the bench (in bench order)
        info["on_bench"] = p.get("position", 0) > 11
        info["is_captain"] = p.get("is_captain", False)
        info["is_vice_captain"] = p.get("is_vice_captain", False)
        info["multiplier"] = p.get("multiplier")
        players.append(info)

    return {
        "entry_id": eid,
        "gameweek": gw,
        "summary": {
            "gameweek_points": history.get("points"),
            "total_points": history.get("total_points"),
            "overall_rank": history.get("overall_rank"),
            "bank": round(history.get("bank", 0) / 10, 1),          # £m
            "squad_value": round(history.get("value", 0) / 10, 1),  # £m
            "transfers_made": history.get("event_transfers"),
        },
        "squad": players,
    }


@tool_ownership("fpl_agent")
@function_tool
async def get_my_fpl_leagues(ctx: Context, entry_id: Optional[int] = None) -> dict:
    """
    Description:
        Lists the classic (points-based) mini-leagues the owner's Fantasy Premier League
        manager is a member of, with the manager's current rank in each. Use this to
        discover league ids (e.g. to then fetch a specific league's standings) or to
        answer "which leagues am I in / what's my rank".

    Parameters:
    ctx : Context
        Context in which the tool operates

    entry_id: Optional[int] = None
        The FPL manager ("entry") id. If omitted, the owner's own id from the
        FPL_ENTRY_ID environment variable is used.

    Output:
        JSON object with the manager's name and a list of their classic leagues
        (league id, name, and the manager's rank in it).
    """
    eid = _fpl_entry_id(entry_id)
    if eid is None:
        return {
            "Error": "No FPL manager id is configured.",
            "Tip": "Set FPL_ENTRY_ID in the environment or pass entry_id explicitly.",
        }
    logger.info(f"Getting FPL leagues for entry={eid}")
    try:
        entry = await fetch_fpl(f"entry/{eid}/")
    except Exception as e:
        logger.error("Error while getting FPL leagues", exc_info=True)
        return {"Message": "Error while getting the manager's leagues", "Error": str(e)}

    leagues = [
        {"id": l.get("id"), "name": l.get("name"), "my_rank": l.get("entry_rank")}
        for l in (entry.get("leagues", {}) or {}).get("classic", [])
    ]
    return {
        "manager_name": f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip(),
        "team_name": entry.get("name"),
        "leagues_count": len(leagues),
        "leagues": leagues,
    }


@tool_ownership("fpl_agent")
@function_tool
async def get_fpl_league_standings(ctx: Context,
                                   league_id: Optional[int] = None,
                                   limit: int = 25) -> dict:
    """
    Description:
        Returns the standings (table) of a Fantasy Premier League classic mini-league:
        the ranked managers with their team name, total points and last-gameweek
        points. The owner's own row is flagged. Use this for "how's my league doing /
        what's the table in my league".

    Parameters:
    ctx : Context
        Context in which the tool operates

    league_id: Optional[int] = None
        The classic league id to fetch. If omitted, the owner's default league from the
        FPL_LEAGUE_ID environment variable is used. Use get_my_fpl_leagues to discover
        league ids.

    limit: int = 25
        Maximum number of ranked entries to return (from the top of the table).

    Output:
        JSON object with the league name and the ranked standings (rank, team name,
        manager name, total points, last-gameweek points), with the owner's row marked.
    """
    lid = _fpl_league_id(league_id)
    if lid is None:
        return {
            "Error": "No FPL league id is configured.",
            "Tip": "Set FPL_LEAGUE_ID in the environment, pass league_id explicitly, or call "
                   "get_my_fpl_leagues to find your league ids.",
        }
    logger.info(f"Getting FPL standings for league={lid}")
    try:
        data = await fetch_fpl(f"leagues-classic/{lid}/standings/")
    except Exception as e:
        logger.error("Error while getting FPL league standings", exc_info=True)
        return {"Message": "Error while getting the league standings", "Error": str(e)}

    my_entry = _fpl_entry_id(None)
    results = (data.get("standings", {}) or {}).get("results", [])
    table = [
        {
            "rank": r.get("rank"),
            "team_name": r.get("entry_name"),
            "manager": r.get("player_name"),
            "total_points": r.get("total"),
            "gameweek_points": r.get("event_total"),
            "is_me": my_entry is not None and r.get("entry") == my_entry,
        }
        for r in results[: max(1, limit)]
    ]
    return {
        "league_id": lid,
        "league_name": (data.get("league", {}) or {}).get("name"),
        "entries_shown": len(table),
        "standings": table,
    }


def _player_match_status(team_state: dict, team_id) -> str:
    """Human-readable state of a player's match this gameweek."""
    st = team_state.get(team_id)
    if not st:
        return "not started"
    if st.get("live"):
        minutes = st.get("minutes")
        return f"live {minutes}'" if minutes is not None else "live"
    if st.get("finished"):
        return "finished"
    return "not started"


def _player_playing_status(match_state: dict, stats: dict) -> str:
    """Whether the player is actually on the pitch, from the live feed's `starts` /
    `minutes` (which only populate once the real lineup is confirmed at kickoff).

    A player can be in the owner's FPL XI yet start the real match on the bench: that
    shows here as 'benched (not on)' while the match is live, or 'did not play (unused
    sub)' once it has finished with zero minutes."""
    starts = stats.get("starts") or 0
    minutes = stats.get("minutes") or 0
    live = bool(match_state and match_state.get("live"))
    finished = bool(match_state and match_state.get("finished"))
    if not live and not finished:
        return "match not started"
    if finished:
        if minutes and starts:
            return "played (started)"
        if minutes:
            return "played (subbed on)"
        return "did not play (unused sub)"
    # match is live
    if starts:
        return "playing (started)"
    if minutes:
        return "playing (subbed on)"
    return "benched (not on)"


@tool_ownership("fpl_agent")
@function_tool
async def get_fpl_live(ctx: Context,
                       gameweek: Optional[int] = None,
                       include_my_players: bool = True,
                       entry_id: Optional[int] = None) -> dict:
    """
    Description:
        Real-time state of the gameweek: which Premier League matches are being played
        right now, what is happening in them (live score, minute, goals, assists, cards)
        and — for the owner — how each of their FPL players is doing live (minutes,
        goals, bonus, live points, and whether that player's match is in play). Use this
        for "are there matches on now / what's the score / how are my players doing".

        Each of the owner's players also carries `started` and `playing_status`, which
        reflect the REAL match — 'playing (started)', 'playing (subbed on)', 'benched
        (not on)', 'played/did not play' — so you can tell a player who is actually on
        the pitch from one the owner has in their FPL XI but who is sitting on the real
        bench. `my_players.starters_not_playing` lists exactly those benched/unused XI
        players. This becomes known only once lineups are confirmed at kickoff.

    Parameters:
    ctx : Context
        Context in which the tool operates

    gameweek: Optional[int] = None
        The gameweek to inspect. If omitted, the round currently in play is used
        (the current gameweek while it is unfinished, else the next one).

    include_my_players: bool = True
        When true, also return the owner's squad with live per-player stats. Set false
        to only get the match scores/events (e.g. when no manager id is configured).

    entry_id: Optional[int] = None
        The FPL manager ("entry") id for the squad section. If omitted, the owner's own
        id from FPL_ENTRY_ID is used.

    Output:
        JSON object with `any_live` (is any match in play), `live_matches` (in-progress
        matches with live score, minute and events), `finished_matches` (final scores
        for matches already played this gameweek) and, when requested, `my_players`
        (each squad player's live stats, match status and provisional points).
    """
    logger.info(f"Getting FPL live state for gameweek={gameweek or 'in-play'}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = gameweek if gameweek is not None else relevant_gameweek(bootstrap)
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, elements_by_id, positions_by_id = index_bootstrap(bootstrap)
        fixtures = await fetch_fpl(f"fixtures/?event={gw}")
    except Exception as e:
        logger.error("Error while getting FPL live state", exc_info=True)
        return {"Message": "Error while getting the live FPL state", "Error": str(e)}

    # A fixture is "live" once it has started but before it is provisionally finished
    # (finished_provisional flips to true at full time, before bonus is finalized).
    live_matches, finished_matches = [], []
    for f in fixtures:
        if not f.get("started"):
            continue
        home = teams_by_id.get(f.get("team_h"), {})
        away = teams_by_id.get(f.get("team_a"), {})
        base = {
            "home": home.get("name"),
            "away": away.get("name"),
            "score": f"{f.get('team_h_score')}-{f.get('team_a_score')}",
        }
        if f.get("finished_provisional"):
            finished_matches.append(base)
        else:
            live = dict(base)
            live["minutes"] = f.get("minutes")
            live["events"] = summarize_fixture_events(f, elements_by_id, teams_by_id)
            live_matches.append(live)

    result: dict = {
        "gameweek": gw,
        "any_live": bool(live_matches),
        "live_matches": live_matches,
        "finished_matches": finished_matches,
    }
    if not live_matches and not finished_matches:
        result["note"] = "No matches in this gameweek have kicked off yet."

    if include_my_players:
        eid = _fpl_entry_id(entry_id)
        if eid is None:
            result["my_players"] = {
                "Error": "No FPL manager id is configured.",
                "Tip": "Set FPL_ENTRY_ID in the environment or pass entry_id explicitly.",
            }
            return result
        try:
            live_data = await fetch_fpl(f"event/{gw}/live/")
            picks_data = await fetch_fpl(f"entry/{eid}/event/{gw}/picks/")
        except Exception as e:
            logger.error("Error while getting live squad performance", exc_info=True)
            result["my_players"] = {"Error": str(e)}
            return result

        live_by_element = {e.get("id"): e.get("stats", {}) for e in live_data.get("elements", [])}
        # Map each team to the state of its match this gameweek (prefer a live fixture
        # if a team somehow has more than one, e.g. a double gameweek).
        team_state: dict = {}
        for f in fixtures:
            info = {
                "live": bool(f.get("started") and not f.get("finished_provisional")),
                "finished": bool(f.get("finished_provisional")),
                "minutes": f.get("minutes"),
            }
            for team_id in (f.get("team_h"), f.get("team_a")):
                prev = team_state.get(team_id)
                if prev is None or (info["live"] and not prev["live"]):
                    team_state[team_id] = info

        players = []
        provisional_points = 0
        for p in picks_data.get("picks", []):
            element = elements_by_id.get(p.get("element"), {})
            stats = live_by_element.get(p.get("element"), {})
            team_id = element.get("team")
            multiplier = p.get("multiplier", 0)
            points = stats.get("total_points", 0) or 0
            counted = points * multiplier
            provisional_points += counted
            players.append({
                "name": element.get("web_name"),
                "team": teams_by_id.get(team_id, {}).get("short_name"),
                "position": positions_by_id.get(element.get("element_type"), {}).get("singular_name_short"),
                "on_bench": p.get("position", 0) > 11,  # on the owner's FPL bench
                "is_captain": p.get("is_captain", False),
                "is_vice_captain": p.get("is_vice_captain", False),
                "multiplier": multiplier,
                "match_status": _player_match_status(team_state, team_id),
                # whether the player actually featured in the real match (started / came
                # off the bench / benched) — distinct from the owner's FPL bench above.
                "started": bool(stats.get("starts")),
                "playing_status": _player_playing_status(team_state.get(team_id), stats),
                "minutes": stats.get("minutes"),
                "goals": stats.get("goals_scored"),
                "assists": stats.get("assists"),
                "yellow_cards": stats.get("yellow_cards"),
                "red_cards": stats.get("red_cards"),
                "bonus": stats.get("bonus"),
                "points": points,               # raw FPL points for the player
                "points_counted": counted,      # after captain/bench multiplier
            })

        # Players the owner fielded (in their FPL XI, not their own bench) who are NOT on
        # the pitch in the real match — benched now, or an unused sub once it is over.
        # These are the ones silently costing points, so surface them explicitly.
        starters_not_playing = [
            p["name"] for p in players
            if not p["on_bench"] and p["playing_status"] in ("benched (not on)", "did not play (unused sub)")
        ]

        result["my_players"] = {
            "entry_id": eid,
            "provisional_gameweek_points": provisional_points,
            "starters_not_playing": starters_not_playing,
            "note": "Provisional live points (starting XI x multiplier). 'playing_status' "
                    "reflects the REAL match (started/subbed on/benched), which FPL only "
                    "exposes once lineups are confirmed at kickoff; before that a benched "
                    "player is indistinguishable from one whose match has not started. FPL "
                    "applies bench auto-substitutions only after a player's match finishes.",
            "players": players,
        }

    return result


@tool_ownership("fpl_agent")
@function_tool
async def who_owns_player_in_league(ctx: Context,
                                    player: str,
                                    league_id: Optional[int] = None,
                                    gameweek: Optional[int] = None,
                                    max_managers: int = _LEAGUE_SCAN_DEFAULT) -> dict:
    """
    Description:
        Finds which managers in a Fantasy Premier League mini-league own a given player,
        and how many. Answers questions like "does anyone else in my league have Dalot"
        or "how many people have Haaland". It scans each manager's squad for the
        gameweek, so it also knows who has captained the player or has him on their bench.

    Parameters:
    ctx : Context
        Context in which the tool operates

    player: str
        The player's name (e.g. 'Haaland', 'Dalot', 'Bruno Fernandes'). Resolved against
        the FPL player list; if the name is ambiguous the tool returns the candidates so
        you can ask the user which one they meant.

    league_id: Optional[int] = None
        The classic league to scan. If omitted, the owner's default league from
        FPL_LEAGUE_ID is used. Use get_my_fpl_leagues to discover league ids.

    gameweek: Optional[int] = None
        The gameweek whose squads to check. If omitted, the current gameweek is used.

    max_managers: int = 50
        Safety cap on how many managers (from the top of the table by rank) to scan.

    Output:
        JSON object with the resolved player, the league, how many of the scanned
        managers own the player (with in-league ownership %) and the list of owners
        (manager, team name, rank, whether captained or benched by them).
    """
    lid = _fpl_league_id(league_id)
    if lid is None:
        return {
            "Error": "No FPL league id is configured.",
            "Tip": "Set FPL_LEAGUE_ID, pass league_id, or call get_my_fpl_leagues to find your league ids.",
        }
    logger.info(f"Scanning league {lid} for owners of '{player}'")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = resolve_gameweek(bootstrap, gameweek, prefer="current")
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, elements_by_id, _ = index_bootstrap(bootstrap)
    except Exception as e:
        logger.error("Error while preparing league ownership scan", exc_info=True)
        return {"Message": "Error while scanning the league", "Error": str(e)}

    matches = find_players_by_name(bootstrap, player)
    if not matches:
        return {"Error": f"No FPL player matched '{player}'."}
    if len(matches) > 1:
        return {
            "ambiguous_player": player,
            "candidates": [
                {"name": m.get("web_name"),
                 "full_name": f"{m.get('first_name','')} {m.get('second_name','')}".strip(),
                 "team": teams_by_id.get(m.get("team"), {}).get("short_name")}
                for m in matches[:10]
            ],
            "Tip": "Ask the user which player they meant, then call again with a more specific name.",
        }

    target = matches[0]
    target_id = target["id"]
    try:
        league_name, pairs = await load_league_squads(lid, gw, max(1, max_managers))
    except Exception as e:
        logger.error("Error while fetching league squads", exc_info=True)
        return {"Message": "Error while fetching the league squads", "Error": str(e)}

    owners, scanned, failed = [], 0, 0
    for row, picks in pairs:
        if picks is None:
            failed += 1
            continue
        scanned += 1
        pick = next((p for p in picks if p.get("element") == target_id), None)
        if pick:
            owners.append({
                "manager": row.get("player_name"),
                "team_name": row.get("entry_name"),
                "rank": row.get("rank"),
                "is_captain": pick.get("is_captain", False),
                "on_their_bench": pick.get("position", 0) > 11,
            })

    owners.sort(key=lambda o: o.get("rank") or 10**9)
    return {
        "player": target.get("web_name"),
        "team": teams_by_id.get(target.get("team"), {}).get("short_name"),
        "league_id": lid,
        "league_name": league_name,
        "gameweek": gw,
        "managers_scanned": scanned,
        "owner_count": len(owners),
        "ownership_pct_in_league": round(100 * len(owners) / scanned, 1) if scanned else None,
        "owners": owners,
        "unavailable_squads": failed or None,
    }


@tool_ownership("fpl_agent")
@function_tool
async def get_league_ownership(ctx: Context,
                               league_id: Optional[int] = None,
                               gameweek: Optional[int] = None,
                               top_n: int = 20,
                               max_managers: int = _LEAGUE_SCAN_DEFAULT) -> dict:
    """
    Description:
        Aggregates squad ownership across a Fantasy Premier League mini-league: which
        players are the most-owned within the league (effective in-league ownership) and
        how often each is captained. Use it for "what's popular in my league / most
        picked players / template" questions. To check one specific player instead, use
        who_owns_player_in_league.

    Parameters:
    ctx : Context
        Context in which the tool operates

    league_id: Optional[int] = None
        The classic league to scan. If omitted, the owner's default league from
        FPL_LEAGUE_ID is used.

    gameweek: Optional[int] = None
        The gameweek whose squads to aggregate. If omitted, the current gameweek is used.

    top_n: int = 20
        How many of the most-owned players to return.

    max_managers: int = 50
        Safety cap on how many managers (from the top of the table by rank) to scan.

    Output:
        JSON object with the league, how many managers were scanned and the most-owned
        players (name, team, position, how many managers own them, in-league ownership %
        and how many captained them).
    """
    lid = _fpl_league_id(league_id)
    if lid is None:
        return {
            "Error": "No FPL league id is configured.",
            "Tip": "Set FPL_LEAGUE_ID, pass league_id, or call get_my_fpl_leagues to find your league ids.",
        }
    logger.info(f"Aggregating ownership for league {lid}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = resolve_gameweek(bootstrap, gameweek, prefer="current")
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, elements_by_id, positions_by_id = index_bootstrap(bootstrap)
        league_name, pairs = await load_league_squads(lid, gw, max(1, max_managers))
    except Exception as e:
        logger.error("Error while aggregating league ownership", exc_info=True)
        return {"Message": "Error while aggregating league ownership", "Error": str(e)}

    owned: dict = {}
    captained: dict = {}
    scanned = 0
    for _, picks in pairs:
        if picks is None:
            continue
        scanned += 1
        for p in picks:
            el = p.get("element")
            owned[el] = owned.get(el, 0) + 1
            if p.get("is_captain"):
                captained[el] = captained.get(el, 0) + 1

    ranked = sorted(owned.items(), key=lambda kv: kv[1], reverse=True)[: max(1, top_n)]
    top_owned = []
    for el, count in ranked:
        element = elements_by_id.get(el, {})
        top_owned.append({
            "name": element.get("web_name"),
            "team": teams_by_id.get(element.get("team"), {}).get("short_name"),
            "position": positions_by_id.get(element.get("element_type"), {}).get("singular_name_short"),
            "owned_by": count,
            "ownership_pct_in_league": round(100 * count / scanned, 1) if scanned else None,
            "captained_by": captained.get(el, 0),
        })

    return {
        "league_id": lid,
        "league_name": league_name,
        "gameweek": gw,
        "managers_scanned": scanned,
        "top_owned": top_owned,
    }

