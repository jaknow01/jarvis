"""Tests for finance history pure helpers: watchlist, series ids, aliases, summary
(no network)."""
import pytest

from lib.agents.finance_agent import history as h


def test_default_watchlist(monkeypatch):
    for v in ("OBSERVATIONS_FX", "OBSERVATIONS_EQUITIES", "OBSERVATIONS_METALS"):
        monkeypatch.delenv(v, raising=False)
    assert h.fx_currencies() == ["USD", "EUR", "GBP", "CHF"]
    assert h.equity_symbols() == ["^GSPC"]
    assert h.metal_symbols() == ["GC=F", "SI=F"]


def test_watchlist_env_override(monkeypatch):
    monkeypatch.setenv("OBSERVATIONS_FX", "usd, jpy")
    assert h.fx_currencies() == ["USD", "JPY"]


def test_series_id_helpers():
    assert h.fx_series("usd") == "fx:USD/PLN"
    assert h.equity_series("^GSPC") == "index:^GSPC"
    assert h.metal_series("GC=F") == "metal:GC=F"


def test_series_catalog_includes_derived_and_etf(monkeypatch):
    for v in ("OBSERVATIONS_FX", "OBSERVATIONS_EQUITIES", "OBSERVATIONS_METALS", "OBSERVATIONS_SP500_ETF"):
        monkeypatch.delenv(v, raising=False)
    cat = h.series_catalog()
    assert h.SP500_PLN_SERIES in cat        # the real ETF in PLN (derived)
    assert h.SP500_ETF_EUR_SERIES in cat    # the real ETF, raw EUR
    assert "fx:USD/PLN" in cat and "metal:GC=F" in cat


def test_derived_pln_dropped_without_etf(monkeypatch):
    monkeypatch.setenv("OBSERVATIONS_SP500_ETF", "")  # disable the ETF -> no PLN derivation
    assert h.SP500_PLN_SERIES not in h.series_catalog()


def test_derived_pln_dropped_without_eur(monkeypatch):
    monkeypatch.delenv("OBSERVATIONS_SP500_ETF", raising=False)
    monkeypatch.setenv("OBSERVATIONS_FX", "USD,GBP")  # no EUR -> cannot convert the ETF
    assert h.SP500_PLN_SERIES not in h.series_catalog()


@pytest.mark.parametrize("query,expected", [
    ("USD", "fx:USD/PLN"),
    ("usd/pln", "fx:USD/PLN"),
    ("sp500", "index:sp500_pln"),
    ("amundi", "index:sp500_pln"),
    ("sp500_eur", "etf:amundi_sp500_eur"),
    ("^GSPC", "index:^GSPC"),
    ("złoto", "metal:GC=F"),
    ("silver", "metal:SI=F"),
    ("fx:EUR/PLN", "fx:EUR/PLN"),
    ("nonsense", None),
])
def test_resolve_series_alias(monkeypatch, query, expected):
    for v in ("OBSERVATIONS_FX", "OBSERVATIONS_EQUITIES", "OBSERVATIONS_METALS", "OBSERVATIONS_SP500_ETF"):
        monkeypatch.delenv(v, raising=False)
    assert h.resolve_series_alias(query) == expected


def test_summarize_series():
    rows = [
        {"date": "2026-01-01", "value": 100.0},
        {"date": "2026-01-02", "value": 110.0},
        {"date": "2026-01-03", "value": 90.0},
    ]
    s = h.summarize_series(rows)
    assert s["count"] == 3
    assert s["first"]["value"] == 100.0 and s["last"]["value"] == 90.0
    assert s["min"] == 90.0 and s["max"] == 110.0
    assert s["change_pct"] == -10.0


def test_summarize_series_empty():
    assert h.summarize_series([])["count"] == 0
    assert h.summarize_series([{"date": "x", "value": None}])["count"] == 0
