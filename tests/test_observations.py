"""Tests for the observations time-series JSON store (no network, no DB)."""
from datetime import date

from lib.domain.observations import ObservationsStore, _to_iso, _to_date


def test_record_and_read_round_trip(tmp_path):
    store = ObservationsStore(tmp_path / "obs.json")
    store.record_many([
        {"series": "fx:USD/PLN", "date": "2026-01-01", "value": 4.0, "source": "frankfurter"},
        {"series": "fx:USD/PLN", "date": "2026-01-02", "value": 4.1, "source": "frankfurter"},
        {"series": "metal:GC=F", "date": "2026-01-02", "value": 2000.0, "payload": {"currency": "USD"}},
    ])
    usd = store.read_series("fx:USD/PLN")
    assert [r["value"] for r in usd] == [4.0, 4.1]
    assert store.list_series() == ["fx:USD/PLN", "metal:GC=F"]
    assert store.latest_date("fx:USD/PLN") == "2026-01-02"
    gold = store.read_series("metal:GC=F")
    assert gold[0]["payload"] == {"currency": "USD"}


def test_upsert_is_idempotent_by_series_and_date(tmp_path):
    store = ObservationsStore(tmp_path / "obs.json")
    store.record_many([{"series": "x", "date": "2026-01-01", "value": 1.0}])
    store.record_many([{"series": "x", "date": "2026-01-01", "value": 2.0}])  # same key
    rows = store.read_series("x")
    assert len(rows) == 1
    assert rows[0]["value"] == 2.0  # overwritten, not duplicated


def test_read_series_respects_bounds(tmp_path):
    store = ObservationsStore(tmp_path / "obs.json")
    store.record_many([
        {"series": "x", "date": "2026-01-01", "value": 1.0},
        {"series": "x", "date": "2026-01-05", "value": 2.0},
        {"series": "x", "date": "2026-01-10", "value": 3.0},
    ])
    rows = store.read_series("x", start=date(2026, 1, 3), end=date(2026, 1, 7))
    assert [r["date"] for r in rows] == ["2026-01-05"]


def test_empty_and_missing(tmp_path):
    store = ObservationsStore(tmp_path / "obs.json")
    assert store.read_series("nope") == []
    assert store.latest_date("nope") is None
    assert store.list_series() == []


def test_date_normalizers():
    assert _to_iso(date(2026, 3, 4)) == "2026-03-04"
    assert _to_iso("2026-03-04T12:00:00") == "2026-03-04"
    assert _to_date("2026-03-04") == date(2026, 3, 4)
