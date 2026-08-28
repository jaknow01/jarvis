"""Token counts -> USD cost via a configurable per-model price table.

The price table (``evals/price_table.yaml``) is keyed by the same ``provider/model`` string
the app uses for ``AGENT_<NAME>_MODEL`` (bare model => provider ``openai``). Prices are USD
per 1M tokens (the unit vendors publish), split into input and output. A model absent from
the table is reported as ``unpriced`` (cost ``None``) rather than silently costed at zero, so
gaps surface in the report instead of hiding.

Judge cost is computed by the same function but kept as its own line item by the caller
(see :mod:`evals.harness.report`): agent cost and judge cost never mix.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_TABLE_PATH = Path(__file__).resolve().parent.parent / "price_table.yaml"


@dataclass
class ModelPrice:
    """USD per 1M input / output tokens."""

    input_per_mtok: float
    output_per_mtok: float


@dataclass
class RoleCost:
    """Cost breakdown for one role (or one (role, model) pair)."""

    role: str
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    input_cost_usd: float | None  # None == unpriced (model missing from the table)
    output_cost_usd: float | None

    @property
    def total_cost_usd(self) -> float | None:
        if self.input_cost_usd is None or self.output_cost_usd is None:
            return None
        return round(self.input_cost_usd + self.output_cost_usd, 6)

    @property
    def unpriced(self) -> bool:
        return self.input_cost_usd is None or self.output_cost_usd is None


class PriceTable:
    """Loads ``provider/model -> ModelPrice`` and prices token counts.

    Lookup normalizes a bare model name to ``openai/<model>`` to match the app's default
    provider, and tolerates an exact ``provider/model`` key. Unknown models return ``None``.
    """

    def __init__(self, prices: dict[str, ModelPrice]):
        self._prices = prices
        # Secondary index keyed by the model id *after* the provider prefix, so a model that
        # the runtime reports without its provider (e.g. OpenAILike reports
        # "deepseek/deepseek-v4-flash-0731" for the app's "openrouter/deepseek/...") still
        # prices. Collisions (same tail under two providers) keep the first entry.
        self._by_tail: dict[str, ModelPrice] = {}
        for key, price in prices.items():
            tail = key.split("/", 1)[1] if "/" in key else key
            self._by_tail.setdefault(tail, price)

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "PriceTable":
        p = Path(path) if path else _DEFAULT_TABLE_PATH
        raw = yaml.safe_load(p.read_text()) if p.exists() else {}
        prices: dict[str, ModelPrice] = {}
        for key, val in (raw.get("models") or {}).items():
            prices[key] = ModelPrice(
                input_per_mtok=float(val["input_per_mtok"]),
                output_per_mtok=float(val["output_per_mtok"]),
            )
        return cls(prices)

    def _lookup(self, model: str | None) -> ModelPrice | None:
        if not model:
            return None
        # 1) exact provider/model key
        if model in self._prices:
            return self._prices[model]
        # 2) bare model => openai/<model>
        if "/" not in model and f"openai/{model}" in self._prices:
            return self._prices[f"openai/{model}"]
        # 3) match by the model id after the provider prefix, in both directions:
        #    the reported model may carry a partial prefix ("deepseek/deepseek-v4-flash-0731")
        #    while the table key carries the full one ("openrouter/deepseek/deepseek-v4-flash-0731").
        reported_tail = model.split("/", 1)[1] if "/" in model else model
        if reported_tail in self._by_tail:
            return self._by_tail[reported_tail]
        if model in self._by_tail:
            return self._by_tail[model]
        return None

    def price_tokens(
        self, role: str, model: str | None, prompt_tokens: int, completion_tokens: int
    ) -> RoleCost:
        price = self._lookup(model)
        if price is None:
            return RoleCost(role, model, prompt_tokens, completion_tokens, None, None)
        in_cost = round(prompt_tokens / 1_000_000 * price.input_per_mtok, 6)
        out_cost = round(completion_tokens / 1_000_000 * price.output_per_mtok, 6)
        return RoleCost(role, model, prompt_tokens, completion_tokens, in_cost, out_cost)


def cost_from_usage(
    by_role_model: dict[tuple[str, str | None], dict[str, int]],
    table: PriceTable,
) -> list[RoleCost]:
    """Turn ``UsageCapture.by_role_model()`` into a list of :class:`RoleCost`.

    Each (role, model) pair is priced independently, so a role that ran on more than one
    model across a turn (unusual, but possible) is not collapsed into a single price.
    """
    out: list[RoleCost] = []
    for (role, model), agg in sorted(by_role_model.items(), key=lambda kv: kv[0][0]):
        out.append(
            table.price_tokens(role, model, agg["prompt_tokens"], agg["completion_tokens"])
        )
    return out


def total_usd(costs: list[RoleCost]) -> float | None:
    """Sum of priced costs; ``None`` if every entry is unpriced, else sums what is priced."""
    priced = [c.total_cost_usd for c in costs if c.total_cost_usd is not None]
    if not priced:
        return None
    return round(sum(priced), 6)
