"""math_agent tools: deterministic numeric calculations (averages, spread, min/max/sum,
percentage change, linear trend, correlation) over sets of numbers provided by the caller.

General-purpose and data-source-agnostic: the coordinator passes in the numbers (e.g. a
series returned by finance_agent's history tool, or figures from any other agent) and gets
back exact, code-computed results — no LLM arithmetic. Registered into
TOOLS_BY_AGENT["math_agent"] via @tool_ownership."""

import logging
import math
import statistics
from typing import Optional

from llama_index.core.workflow import Context

from lib.core import tool_ownership, function_tool

logger = logging.getLogger(__name__)


def _coerce(numbers) -> tuple[list[float], list]:
    """Return (usable floats, rejected entries). Tolerates ints/floats/numeric strings."""
    good, bad = [], []
    for n in (numbers or []):
        try:
            good.append(float(n))
        except (TypeError, ValueError):
            bad.append(n)
    return good, bad


@tool_ownership("math_agent")
@function_tool
async def calculate_statistics(ctx: Context, numbers: list[float]) -> dict:
    """
    Description:
        Computes summary statistics over a list of numbers in one call: count, sum, mean
        (average), median, minimum, maximum, range (max-min), population and sample standard
        deviation and variance. Use for "średnia/suma/min/max/odchylenie z tych wartości".

    Parameters:
    ctx : Context
        Context in which the tool operates.

    numbers: list[float]
        The numbers to summarize (e.g. [3.71, 3.68, 3.74]). Numeric strings are accepted.

    Output:
        JSON with count, sum, mean, median, min, max, range, stdev_population,
        stdev_sample (null for n<2) and the matching variances; or an error if empty.
    """
    values, rejected = _coerce(numbers)
    if not values:
        return {"error": "No usable numbers provided.", "rejected": rejected or None}
    n = len(values)
    result = {
        "count": n,
        "sum": sum(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "range": max(values) - min(values),
        "stdev_population": statistics.pstdev(values),
        "variance_population": statistics.pvariance(values),
        "stdev_sample": statistics.stdev(values) if n >= 2 else None,
        "variance_sample": statistics.variance(values) if n >= 2 else None,
    }
    if rejected:
        result["rejected"] = rejected
    return result


@tool_ownership("math_agent")
@function_tool
async def percentage_change(ctx: Context, old_value: float, new_value: float) -> dict:
    """
    Description:
        Computes the absolute and percentage change from an old value to a new value
        (e.g. how much a price rose/fell). Use for "o ile procent zmieniło się X".

    Parameters:
    ctx : Context
        Context in which the tool operates.

    old_value: float
        The starting/earlier value.

    new_value: float
        The ending/later value.

    Output:
        JSON with absolute_change (new-old), percent_change and direction
        (up/down/unchanged); percent_change is null if old_value is 0.
    """
    try:
        old_value = float(old_value)
        new_value = float(new_value)
    except (TypeError, ValueError):
        return {"error": "old_value and new_value must be numbers."}
    abs_change = new_value - old_value
    pct = (abs_change / old_value * 100) if old_value != 0 else None
    direction = "unchanged" if abs_change == 0 else ("up" if abs_change > 0 else "down")
    return {"absolute_change": abs_change, "percent_change": pct, "direction": direction}


@tool_ownership("math_agent")
@function_tool
async def linear_trend(ctx: Context, numbers: list[float]) -> dict:
    """
    Description:
        Fits a least-squares straight line to a sequence of numbers taken in order (x = 0,
        1, 2, …) and reports its slope, intercept, overall direction and the fitted change
        across the whole sequence. Use to quantify a trend ("czy X rośnie i jak szybko").

    Parameters:
    ctx : Context
        Context in which the tool operates.

    numbers: list[float]
        The ordered sequence (e.g. daily values, oldest first). Needs at least 2 points.

    Output:
        JSON with n, slope (change per step), intercept, direction (rising/falling/flat),
        first, last, total_change (last-first) and fitted_change (slope*(n-1)); or an error.
    """
    values, _ = _coerce(numbers)
    if len(values) < 2:
        return {"error": "Need at least 2 numbers to fit a trend.", "count": len(values)}
    n = len(values)
    xs = list(range(n))
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(values)
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denom if denom else 0.0
    intercept = mean_y - slope * mean_x
    direction = "flat" if math.isclose(slope, 0.0, abs_tol=1e-12) else ("rising" if slope > 0 else "falling")
    return {
        "n": n,
        "slope": slope,
        "intercept": intercept,
        "direction": direction,
        "first": values[0],
        "last": values[-1],
        "total_change": values[-1] - values[0],
        "fitted_change": slope * (n - 1),
    }


@tool_ownership("math_agent")
@function_tool
async def correlation(ctx: Context, x: list[float], y: list[float]) -> dict:
    """
    Description:
        Computes the Pearson correlation coefficient between two equal-length series
        (e.g. does gold move with USD/PLN). Use for "czy X i Y są ze sobą powiązane".

    Parameters:
    ctx : Context
        Context in which the tool operates.

    x: list[float]
        First series.

    y: list[float]
        Second series (must be the same length as x, at least 2 points).

    Output:
        JSON with n and pearson_r (-1..1); or an error if lengths differ or variance is zero.
    """
    xv, _ = _coerce(x)
    yv, _ = _coerce(y)
    if len(xv) != len(yv):
        return {"error": f"Series have different lengths ({len(xv)} vs {len(yv)})."}
    if len(xv) < 2:
        return {"error": "Need at least 2 paired points."}
    try:
        r = statistics.correlation(xv, yv)
    except statistics.StatisticsError as e:
        return {"error": f"Cannot compute correlation: {e}"}
    return {"n": len(xv), "pearson_r": r}
