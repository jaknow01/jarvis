"""Unit tests for pure helpers in lib.tools_utils (no network / API keys needed)."""
import asyncio

from lib.tools_utils import validate_currency_code, simplify_directions_response


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
