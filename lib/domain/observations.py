"""Daily time-series store ("observations") for external readings we want to analyze
over time rather than as one-off snapshots — FX rates, index/ETF levels, metals.

One logical record is one metric on one calendar day, keyed by a stable ``series`` string
('fx:USD/PLN', 'index:^GSPC', 'index:sp500_pln', 'metal:GC=F') plus ``date``. Writing is
idempotent per (series, date): the daily collector and the year-long backfill both upsert,
so a re-run overwrites a day instead of duplicating it.

Persists to Postgres when ``DATABASE_URL`` is set (see ``app/db/observations_repo.py``),
otherwise to the on-disk JSON store at ``data/observations_data/observations.json`` —
mirrors the memory/scheduler backend-selection pattern. This module owns storage; the
agent-facing history tools live in ``lib/agents/finance_agent``.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

OBSERVATIONS_PATH = Path("data/observations_data/observations.json")


def _to_iso(d) -> str:
    """Normalize a ``date``/``datetime``/ISO-string to a ``YYYY-MM-DD`` string."""
    if isinstance(d, (date, datetime)):
        return d.isoformat()[:10]
    return str(d)[:10]


def _to_date(d) -> date:
    """Normalize a ``date``/``datetime``/ISO-string to a ``datetime.date``."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


class ObservationsStore:
    """On-disk JSON backend. Shape: ``{series: {"YYYY-MM-DD": {value, payload, source}}}``
    (a per-date map keeps upserts trivial and idempotent). Atomic writes, lock-guarded."""

    def __init__(self, path: Path = OBSERVATIONS_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"series": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"observations: could not read {self.path}: {e}")
            return {"series": {}}
        if not isinstance(data, dict) or not isinstance(data.get("series"), dict):
            logger.error(f"observations: unexpected shape in {self.path}; ignoring")
            return {"series": {}}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def record_many(self, rows: Iterable[dict]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        with self._lock:
            data = self._load()
            series_map = data["series"]
            for r in rows:
                s = series_map.setdefault(r["series"], {})
                s[_to_iso(r["date"])] = {
                    "value": r.get("value"),
                    "payload": r.get("payload"),
                    "source": r.get("source"),
                }
            self._save(data)
        return len(rows)

    def read_series(self, series: str, start=None, end=None) -> list[dict]:
        data = self._load()
        s = data["series"].get(series, {})
        start_iso = _to_iso(start) if start is not None else None
        end_iso = _to_iso(end) if end is not None else None
        out = []
        for d in sorted(s):
            if start_iso and d < start_iso:
                continue
            if end_iso and d > end_iso:
                continue
            out.append({"date": d, "value": s[d].get("value"), "payload": s[d].get("payload")})
        return out

    def latest_date(self, series: str) -> Optional[str]:
        data = self._load()
        s = data["series"].get(series, {})
        return max(s) if s else None

    def list_series(self) -> list[str]:
        return sorted(self._load()["series"])


class _PostgresObservationsBackend:
    """Thin adapter over ``app.db.observations_repo`` (functional) with the same API as
    ``ObservationsStore``, normalizing dates to ISO strings on read."""

    def record_many(self, rows: Iterable[dict]) -> int:
        from app.db import observations_repo
        normalized = [
            {
                "series": r["series"],
                "date": _to_date(r["date"]),
                "value": r.get("value"),
                "payload": r.get("payload"),
                "source": r.get("source"),
            }
            for r in rows
        ]
        return observations_repo.upsert_many(normalized)

    def read_series(self, series: str, start=None, end=None) -> list[dict]:
        from app.db import observations_repo
        rows = observations_repo.read_series(
            series,
            _to_date(start) if start is not None else None,
            _to_date(end) if end is not None else None,
        )
        return [{"date": _to_iso(r["date"]), "value": r["value"], "payload": r["payload"]} for r in rows]

    def latest_date(self, series: str) -> Optional[str]:
        from app.db import observations_repo
        d = observations_repo.latest_date(series)
        return _to_iso(d) if d is not None else None

    def list_series(self) -> list[str]:
        from app.db import observations_repo
        return observations_repo.list_series()


def _select_backend():
    """Choose the backend at first use (after .env is loaded): Postgres when DATABASE_URL
    is set, otherwise the on-disk JSON store."""
    try:
        from app.db import connection as dbconn
        if dbconn.is_configured():
            from app.db.schema import init_db
            init_db()
            logger.info("Observations store backend: Postgres")
            return _PostgresObservationsBackend()
    except Exception as e:  # noqa: BLE001 - never let storage selection crash the app
        logger.error(f"Postgres observations backend unavailable ({e}); falling back to JSON file")
    logger.info("Observations store backend: JSON file")
    return ObservationsStore()


class _ObservationsProxy:
    """Delegates to the backend chosen lazily on first use, so DATABASE_URL from .env
    (loaded after import) is respected."""

    def __init__(self):
        self._impl = None

    def _store(self):
        if self._impl is None:
            self._impl = _select_backend()
        return self._impl

    def record_many(self, rows: Iterable[dict]) -> int:
        return self._store().record_many(rows)

    def read_series(self, series: str, start=None, end=None) -> list[dict]:
        return self._store().read_series(series, start, end)

    def latest_date(self, series: str) -> Optional[str]:
        return self._store().latest_date(series)

    def list_series(self) -> list[str]:
        return self._store().list_series()


# module-wide singleton (backend chosen on first use)
observations = _ObservationsProxy()
