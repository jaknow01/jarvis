"""Model-sweep engine: grid one role over {model, reasoning effort}, hold the rest fixed.

For each candidate the sweep builds a config = baseline overrides + {swept role -> candidate},
runs every golden through the real network, scores with the constant judge, and aggregates
quality (mean metric scores + pass rate) against cost (mean USD per turn, and the swept
role's own mean USD). The result feeds :mod:`evals.harness.report`.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from evals.harness.config import EvalConfig, JudgeConfig, RoleModel
from evals.harness.cost import PriceTable
from evals.harness.datasets import EvalGolden
from evals.harness.driver import GoldenRun, run_golden
from evals.harness.metrics import MetricResult, score_golden


@dataclass
class GoldenOutcome:
    """One golden under one candidate: the run plus its metric results."""

    golden_id: str
    run: GoldenRun
    metrics: list[MetricResult]


@dataclass
class CandidateResult:
    """Aggregated quality + cost for one candidate configuration of the swept role."""

    role: str
    label: str
    model: str
    reasoning_effort: str | None
    outcomes: list[GoldenOutcome] = field(default_factory=list)

    # aggregates (filled by finalize())
    metric_means: dict[str, float] = field(default_factory=dict)
    metric_pass_rate: dict[str, float] = field(default_factory=dict)
    mean_total_cost_usd: float | None = None
    mean_role_cost_usd: float | None = None
    unpriced_models: list[str] = field(default_factory=list)

    def finalize(self) -> "CandidateResult":
        # metric aggregates across goldens
        by_metric: dict[str, list[float]] = {}
        by_metric_pass: dict[str, list[bool]] = {}
        for oc in self.outcomes:
            for m in oc.metrics:
                if m.score is not None:
                    by_metric.setdefault(m.metric, []).append(m.score)
                if m.passed is not None:
                    by_metric_pass.setdefault(m.metric, []).append(m.passed)
        self.metric_means = {k: round(statistics.mean(v), 4) for k, v in by_metric.items() if v}
        self.metric_pass_rate = {
            k: round(sum(v) / len(v), 4) for k, v in by_metric_pass.items() if v
        }

        # cost aggregates
        totals = [oc.run.total_cost_usd for oc in self.outcomes if oc.run.total_cost_usd is not None]
        self.mean_total_cost_usd = round(statistics.mean(totals), 6) if totals else None

        role_costs: list[float] = []
        unpriced: set[str] = set()
        for oc in self.outcomes:
            for c in oc.run.costs:
                if c.role == self.role and c.total_cost_usd is not None:
                    role_costs.append(c.total_cost_usd)
                if c.unpriced:
                    unpriced.add(c.model or "?")
        self.mean_role_cost_usd = round(statistics.mean(role_costs), 6) if role_costs else None
        self.unpriced_models = sorted(unpriced)
        return self


async def sweep_role(
    role: str,
    candidates: list[RoleModel],
    goldens: list[EvalGolden],
    judge: JudgeConfig,
    table: PriceTable,
    baseline: EvalConfig | None = None,
    composer_threshold: float = 0.7,
    mock: bool = True,
) -> list[CandidateResult]:
    """Run the full grid for ``role`` over ``candidates`` across ``goldens``.

    Args:
        role: the role being swept (e.g. "composer", "coordinator", "weather_agent").
        candidates: the {model, effort} points to try for that role.
        goldens: the fixed dataset every candidate is evaluated on.
        judge: the constant judge used for all metrics across the sweep.
        table: price table for cost.
        baseline: overrides for the *other* roles, held fixed; the swept role's baseline
            entry (if any) is replaced per candidate.
        composer_threshold: pass threshold for the composer-quality GEval.
        mock: run against deterministic mocked tools (default) vs the real tools/APIs.
    """
    base_overrides = dict(baseline.overrides) if baseline else {}
    results: list[CandidateResult] = []

    for cand in candidates:
        if cand.role != role:
            cand = RoleModel(role=role, model=cand.model, reasoning_effort=cand.reasoning_effort)
        label = f"{role}={cand.model}" + (f"@{cand.reasoning_effort}" if cand.reasoning_effort else "")
        overrides = dict(base_overrides)
        overrides[role] = cand
        config = EvalConfig(label=label, overrides=overrides)

        cr = CandidateResult(
            role=role, label=label, model=cand.model, reasoning_effort=cand.reasoning_effort
        )
        for g in goldens:
            run = await run_golden(g.id, g.input, config, table, mock=mock)
            metrics = await score_golden(
                run,
                judge,
                expected_routing=g.expected_routing,
                expected_execution=g.expected_execution,
                composer_threshold=composer_threshold,
            )
            cr.outcomes.append(GoldenOutcome(golden_id=g.id, run=run, metrics=metrics))
        results.append(cr.finalize())

    return results
