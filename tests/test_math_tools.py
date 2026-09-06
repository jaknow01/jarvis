"""Tests for math_agent's deterministic calculation tools (no network, no LLM).

The tools are wrapped by @function_tool; the raw coroutine is reached via `.async_fn`. They take
a Context they don't use, so None is passed."""
import asyncio
import math

from lib.agents.math_agent import tools as mt


def _run(tool, *args):
    return asyncio.run(tool.async_fn(None, *args))


def test_calculate_statistics():
    r = _run(mt.calculate_statistics, [2, 4, 4, 4, 5, 5, 7, 9])
    assert r["count"] == 8
    assert r["sum"] == 40
    assert r["mean"] == 5.0
    assert r["median"] == 4.5
    assert r["min"] == 2 and r["max"] == 9 and r["range"] == 7
    assert math.isclose(r["stdev_population"], 2.0)


def test_calculate_statistics_coerces_and_reports_rejects():
    r = _run(mt.calculate_statistics, ["3", 4, "oops"])
    assert r["count"] == 2
    assert r["sum"] == 7.0
    assert r["rejected"] == ["oops"]


def test_calculate_statistics_empty():
    assert "error" in _run(mt.calculate_statistics, [])


def test_percentage_change():
    r = _run(mt.percentage_change, 100.0, 125.0)
    assert r["absolute_change"] == 25.0
    assert r["percent_change"] == 25.0
    assert r["direction"] == "up"


def test_percentage_change_from_zero():
    r = _run(mt.percentage_change, 0.0, 5.0)
    assert r["percent_change"] is None
    assert r["direction"] == "up"


def test_linear_trend_rising():
    r = _run(mt.linear_trend, [1, 2, 3, 4, 5])
    assert r["direction"] == "rising"
    assert math.isclose(r["slope"], 1.0)
    assert r["total_change"] == 4
    assert math.isclose(r["fitted_change"], 4.0)


def test_linear_trend_needs_two_points():
    assert "error" in _run(mt.linear_trend, [5])


def test_correlation_perfect_positive():
    r = _run(mt.correlation, [1, 2, 3, 4], [2, 4, 6, 8])
    assert math.isclose(r["pearson_r"], 1.0)


def test_correlation_length_mismatch():
    assert "error" in _run(mt.correlation, [1, 2, 3], [1, 2])
