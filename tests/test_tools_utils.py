"""Unit tests for pure helpers in lib.tools_utils (no network / API keys needed)."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from lib.tools_utils import (
    validate_currency_code,
    simplify_directions_response,
    normalize_departure_time,
    relevant_gameweek,
    resolve_gameweek,
    describe_player,
    WARSAW_TZ,
)


def _events(current_id, current_finished, next_id):
    """Minimal bootstrap 'events' fixture for gameweek-resolution tests."""
    events = []
    if current_id is not None:
        events.append({"id": current_id, "is_current": True, "is_next": False, "finished": current_finished})
    if next_id is not None:
        events.append({"id": next_id, "is_current": False, "is_next": True, "finished": False})
    return {"events": events}


def test_relevant_gameweek_prefers_current_while_it_is_live():
    # A current, unfinished GW is the round "today's" matches belong to (the bug: we
    # used to return the NEXT gameweek here, contradicting the squad lookup).
    bs = _events(current_id=1, current_finished=False, next_id=2)
    assert relevant_gameweek(bs) == 1


def test_relevant_gameweek_moves_to_next_once_current_finished():
    bs = _events(current_id=1, current_finished=True, next_id=2)
    assert relevant_gameweek(bs) == 2


def test_relevant_gameweek_falls_back_to_current_at_end_of_season():
    bs = _events(current_id=38, current_finished=True, next_id=None)
    assert relevant_gameweek(bs) == 38


def test_relevant_gameweek_none_when_no_events():
    assert relevant_gameweek({"events": []}) is None


def test_resolve_gameweek_returns_explicit_gameweek_as_is():
    bs = _events(current_id=1, current_finished=False, next_id=2)
    assert resolve_gameweek(bs, 7) == 7


def test_describe_player_flattens_and_scales_price():
    element = {
        "web_name": "Salah", "first_name": "Mohamed", "second_name": "Salah",
        "team": 12, "element_type": 3, "now_cost": 145, "total_points": 20,
        "form": "5.0", "selected_by_percent": "40.0", "status": "a", "news": "",
    }
    teams = {12: {"name": "Liverpool", "short_name": "LIV"}}
    positions = {3: {"singular_name_short": "MID"}}
    out = describe_player(element, teams, positions)
    assert out["name"] == "Salah"
    assert out["team_short"] == "LIV"
    assert out["position"] == "MID"
    assert out["price"] == 14.5  # now_cost is in tenths of £m
    assert out["news"] is None   # empty news normalized to None


def test_validate_currency_code_accepts_known_codes():
    assert validate_currency_code("USD") is True
    assert validate_currency_code("PLN") is True
    assert validate_currency_code("EUR") is True


def test_validate_currency_code_rejects_unknown_codes():
    assert validate_currency_code("ZZZ") is False
    assert validate_currency_code("") is False


def test_simplify_directions_response_extracts_leg_summary():
    sample = [
        {
            "legs": [
                {
                    "start_address": "A Street, Warsaw",
                    "end_address": "B Street, Warsaw",
                    "departure_time": {"text": "10:00"},
                    "arrival_time": {"text": "10:30"},
                    "distance": {"text": "5 km"},
                    "duration": {"text": "30 mins"},
                    "steps": [
                        {
                            "html_instructions": "Walk to stop",
                            "distance": {"text": "200 m"},
                            "duration": {"text": "3 mins"},
                            "travel_mode": "WALKING",
                        },
                        {
                            "html_instructions": "Take the tram",
                            "distance": {"text": "4 km"},
                            "duration": {"text": "20 mins"},
                            "travel_mode": "TRANSIT",
                            "transit_details": {
                                "line": {"short_name": "17", "vehicle": {"name": "Tram"}},
                                "departure_stop": {"name": "Stop A"},
                                "arrival_stop": {"name": "Stop B"},
                                "num_stops": 6,
                            },
                        },
                    ],
                }
            ]
        }
    ]

    result = asyncio.run(simplify_directions_response(sample))

    assert len(result) == 1
    leg = result[0]
    assert leg["start_address"] == "A Street, Warsaw"
    assert leg["total_distance"] == "5 km"
    assert len(leg["steps"]) == 2
    transit_step = leg["steps"][1]
    assert transit_step["transit"]["line"] == "17"
    assert transit_step["transit"]["vehicle"] == "Tram"
    assert transit_step["transit"]["num_stops"] == 6


def test_simplify_directions_response_handles_empty_input():
    assert asyncio.run(simplify_directions_response([])) == []


# -- normalize_departure_time ---------------------------------------------------

def test_normalize_departure_time_now_and_none_pass_through():
    assert normalize_departure_time("now") == "now"
    assert normalize_departure_time(None) == "now"
    assert normalize_departure_time("") == "now"
    assert normalize_departure_time("NOW") == "now"


def test_normalize_departure_time_unparseable_falls_back_to_now():
    assert normalize_departure_time("jutro rano") == "now"
    assert normalize_departure_time("not a time") == "now"


def test_normalize_departure_time_iso_string_becomes_warsaw_epoch():
    result = normalize_departure_time("2026-08-21T08:00:00")
    expected = int(datetime(2026, 8, 21, 8, 0, tzinfo=WARSAW_TZ).timestamp())
    assert result == expected


def test_normalize_departure_time_int_passes_through():
    assert normalize_departure_time(1_700_000_000) == 1_700_000_000
    assert normalize_departure_time(1_700_000_000.9) == 1_700_000_000


def test_normalize_departure_time_naive_datetime_treated_as_warsaw():
    naive = datetime(2026, 8, 21, 8, 0)
    expected = int(datetime(2026, 8, 21, 8, 0, tzinfo=WARSAW_TZ).timestamp())
    assert normalize_departure_time(naive) == expected


def test_normalize_departure_time_aware_datetime_respects_its_tz():
    aware = datetime(2026, 8, 21, 8, 0, tzinfo=ZoneInfo("UTC"))
    assert normalize_departure_time(aware) == int(aware.timestamp())


def test_normalize_departure_time_bare_clock_time_uses_today():
    result = normalize_departure_time("08:00")
    today = datetime.now(WARSAW_TZ).date()
    expected = int(datetime(today.year, today.month, today.day, 8, 0, tzinfo=WARSAW_TZ).timestamp())
    assert result == expected
