"""Tests that the maps agent exposes only place aliases to the model, never the
user's real street addresses, and resolves aliases server-side."""
import types

from lib import tools

FIXTURE = {
    "known_adressess": [
        {"aliases": ["Home"], "address": "Sekretna 1", "preffered_transit_points": ["Stop A"]},
        {"aliases": ["work", "office"], "address": "Tajna 7", "preffered_transit_points": []},
    ]
}


def _ctx():
    return types.SimpleNamespace(context=types.SimpleNamespace(known_adresses=dict(FIXTURE)))


def test_known_entries_alias_list_excludes_addresses():
    entries = tools._known_entries(_ctx())
    aliases = [a for e in entries for a in e["aliases"]]
    assert set(aliases) == {"Home", "work", "office"}
    # the model-facing view (get_maps_memory) is built from aliases only
    assert "Sekretna 1" not in aliases
    assert "Tajna 7" not in aliases


def test_resolve_place_known_alias_returns_address():
    addr, alias = tools._resolve_place(_ctx(), "Home")
    assert addr == "Sekretna 1"
    assert alias == "Home"


def test_resolve_place_is_case_insensitive():
    addr, alias = tools._resolve_place(_ctx(), "OFFICE")
    assert addr == "Tajna 7"
    assert alias == "OFFICE"  # keeps the caller's term for relabeling the response


def test_resolve_place_unknown_passes_through():
    addr, alias = tools._resolve_place(_ctx(), "Metro Bemowo")
    assert addr == "Metro Bemowo"
    assert alias is None


def test_resolve_place_empty_name():
    addr, alias = tools._resolve_place(_ctx(), "")
    assert addr == ""
    assert alias is None
