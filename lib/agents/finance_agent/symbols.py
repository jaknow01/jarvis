"""Ticker lookup for finance_agent: free-text name → Yahoo Finance symbols.

Yahoo symbols are not guessable (WIG20 is ``WIG20.WA``, not ``^WIG20``; CD Projekt is
``CDR.WA``), so instead of listing them in prompts the agent looks them up. Yahoo's keyless
search endpoint already fuzzy-matches over its whole universe (indices, equities, ETFs,
futures, currencies); we fetch a candidate pool, optionally filter by asset type, and
re-rank it locally by text similarity to the query (blended with Yahoo's own order) so the
best match for a short name like "wig20" comes first and near-duplicates (foreign
cross-listings, derivative ETFs) sink.
"""

import re
from difflib import SequenceMatcher

import requests

YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Jarvis personal assistant)"}

# How many candidates to pull from Yahoo before local filtering/re-ranking.
CANDIDATE_POOL = 20

# Yahoo `quoteType` values the agent may filter on.
ASSET_TYPES = ("INDEX", "EQUITY", "ETF", "FUTURE", "CURRENCY", "MUTUALFUND", "CRYPTOCURRENCY")

# Weight of text similarity vs. Yahoo's own rank in the blended score.
_SIM_WEIGHT = 0.7


def _norm(text: str) -> str:
    """Lowercase and drop everything but letters/digits ("S&P 500" -> "sp500")."""
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def _symbol_root(symbol: str) -> str:
    """The symbol without index prefix / exchange suffix ("^GSPC" -> "GSPC", "KGH.WA" -> "KGH")."""
    return symbol.lstrip("^").split(".")[0].split("=")[0]


def similarity(query: str, symbol: str, name: str) -> float:
    """Best text similarity (0..1) of the query to the symbol root or the instrument name."""
    q = _norm(query)
    if not q:
        return 0.0
    return max(
        SequenceMatcher(None, q, _norm(_symbol_root(symbol))).ratio(),
        SequenceMatcher(None, q, _norm(name)).ratio(),
    )


def fetch_candidates(query: str, pool: int = CANDIDATE_POOL) -> list[dict]:
    """Blocking call to Yahoo's search endpoint; returns its raw `quotes` list (ranked)."""
    resp = requests.get(
        YAHOO_SEARCH,
        headers=YAHOO_HEADERS,
        params={"q": query, "quotesCount": pool, "newsCount": 0, "listsCount": 0},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("quotes", []) or []


def rank_matches(query: str, quotes: list[dict], limit: int = 5,
                 asset_type: str | None = None) -> list[dict]:
    """Filter (by `asset_type`) and re-rank Yahoo search results; return the top `limit`.

    Score = 0.7 * text similarity + 0.3 * Yahoo rank position (1.0 for its first hit), so
    an exact name/symbol match wins while Yahoo's relevance still breaks ties."""
    wanted = asset_type.upper() if asset_type else None
    pool = [q for q in quotes if q.get("symbol") and (not wanted or q.get("quoteType") == wanted)]
    n = len(pool)
    scored = []
    for rank, q in enumerate(pool):
        name = q.get("shortname") or q.get("longname") or ""
        sim = similarity(query, q["symbol"], name)
        score = _SIM_WEIGHT * sim + (1 - _SIM_WEIGHT) * (1 - rank / n)
        scored.append((score, rank, {
            "symbol": q["symbol"],
            "name": q.get("longname") or q.get("shortname") or "",
            "type": q.get("quoteType"),
            "exchange": q.get("exchDisp") or q.get("exchange"),
            "match_score": round(score, 2),
        }))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _score, _rank, item in scored[: max(1, limit)]]
