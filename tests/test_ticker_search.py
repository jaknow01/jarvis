"""Tests for finance_agent's ticker lookup ranking (pure; no network)."""
from lib.agents.finance_agent.symbols import rank_matches, similarity

# A trimmed real Yahoo search response for "wig20" (Yahoo's own order).
_WIG20 = [
    {"symbol": "ETFBW20TR.WA", "shortname": "ETFBW20TR", "exchDisp": "WSE", "quoteType": "ETF"},
    {"symbol": "WIG20.WA", "shortname": "WIG20", "exchDisp": "WSE", "quoteType": "INDEX"},
    {"symbol": "PLX.MU", "shortname": "Expat Poland WIG20 UCITS ETF  N", "exchDisp": "Munich", "quoteType": "ETF"},
    {"symbol": "WIG20DVP.WA", "shortname": "WIG20dvp", "exchDisp": "WSE", "quoteType": "INDEX"},
]


def test_exact_name_match_ranks_first_even_if_yahoo_put_it_second():
    top = rank_matches("wig20", _WIG20, limit=3)
    assert top[0]["symbol"] == "WIG20.WA"
    assert len(top) == 3


def test_asset_type_filter():
    top = rank_matches("wig20", _WIG20, asset_type="index")
    assert [m["symbol"] for m in top] == ["WIG20.WA", "WIG20DVP.WA"]


def test_result_shape():
    m = rank_matches("wig20", _WIG20, limit=1)[0]
    assert set(m) == {"symbol", "name", "type", "exchange", "match_score"}
    assert m["exchange"] == "WSE" and m["type"] == "INDEX"


def test_similarity_normalizes_punctuation_and_suffixes():
    assert similarity("S&P 500", "^GSPC", "S&P 500") == 1.0
    assert similarity("kghm", "KGH.WA", "KGHM") == 1.0


def test_empty_inputs():
    assert rank_matches("wig20", [], limit=5) == []
    assert similarity("", "WIG20.WA", "WIG20") == 0.0
