"""Model-sweep engine: grid one role over {model, reasoning effort}, hold the rest fixed.

For each candidate the sweep builds a config = baseline overrides + {swept role -> candidate},
runs every golden through the real network, scores with the constant judge, and aggregates
quality (mean metric scores + pass rate) against cost (mean USD per turn, and the swept
role's own mean USD). The result feeds :mod:`evals.harness.report`.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field

from evals.harness.config import EvalConfig, JudgeConfig, RoleModel
from evals.harness.cost import PriceTable
from evals.harness.datasets import EvalGolden
from evals.harness.driver import GoldenRun, run_golden
from evals.harness.metrics import MetricResult, score_golden
from evals.harness.conversation import (
    ScriptedConversation,
    run_conversation,
    score_conversation,
)


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
    n_errors: int = 0

    def finalize(self) -> "CandidateResult":
        self.n_errors = sum(
            1 for oc in self.outcomes for m in oc.metrics if m.metric == "run_error"
        )
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
    conversations: list[ScriptedConversation] | None = None,
    item_timeout: float | None = 180.0,
) -> list[CandidateResult]:
    """Run the full grid for ``role`` over ``candidates`` across ``goldens`` (and optionally
    scripted multi-turn ``conversations``).

    Args:
        role: the role being swept (e.g. "composer", "coordinator", "weather_agent").
        candidates: the {model, effort} points to try for that role.
        goldens: the single/multi-hop dataset every candidate is evaluated on.
        judge: the constant judge used for all metrics across the sweep.
        table: price table for cost.
        baseline: overrides for the *other* roles, held fixed; the swept role's baseline
            entry (if any) is replaced per candidate.
        composer_threshold: pass threshold for the composer-quality GEval.
        mock: run against deterministic mocked tools (default) vs the real tools/APIs.
        conversations: optional scripted multi-turn conversations. Each conversation's turns
            are scored per-turn (like goldens) and the whole conversation gets the multi-turn
            metrics (conversation_quality_pl, knowledge_retention). Simulated conversations are
            NOT included here (non-deterministic; run them separately).
    """
    base_overrides = dict(baseline.overrides) if baseline else {}
    conversations = conversations or []
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

        # 1) single/multi-hop goldens
        for g in goldens:
            try:
                async def _one() -> GoldenOutcome:
                    run = await run_golden(g.id, g.input, config, table, mock=mock)
                    metrics = await score_golden(
                        run, judge,
                        expected_routing=g.expected_routing,
                        expected_execution=g.expected_execution,
                        composer_threshold=composer_threshold,
                    )
                    return GoldenOutcome(golden_id=g.id, run=run, metrics=metrics)

                # A hung model call (no client timeout) must not block the whole sweep, so cap
                # each item; a timeout/exception is recorded as an error and the sweep goes on.
                cr.outcomes.append(await asyncio.wait_for(_one(), timeout=item_timeout))
            except (Exception, asyncio.TimeoutError) as e:
                cr.outcomes.append(_error_outcome(g.id, config.label, g.input, cr.role, e))

        # 2) scripted multi-turn conversations (allow more time — multiple turns)
        conv_timeout = item_timeout * 4 if item_timeout else None
        for conv in conversations:
            try:
                async def _one_conv() -> None:
                    crun = await run_conversation(conv, config, table, mock=mock)
                    await score_and_append_conversation(cr, crun, judge, composer_threshold)

                await asyncio.wait_for(_one_conv(), timeout=conv_timeout)
            except (Exception, asyncio.TimeoutError) as e:
                cr.outcomes.append(_error_outcome(conv.id, config.label, conv.scenario or conv.id, cr.role, e))

        results.append(cr.finalize())

    return results


def _error_outcome(item_id: str, config_label: str, input_text: str, role: str, err: Exception) -> GoldenOutcome:
    """Record a failed item (timeout, API error, …) as an outcome so the sweep continues and
    the report shows what broke, instead of crashing the whole run."""
    run = GoldenRun(
        golden_id=item_id,
        config_label=config_label,
        input=input_text,
        reply=f"<ERROR: {type(err).__name__}: {err}>",
        usage_by_role={},
        costs=[],
        total_cost_usd=None,
    )
    metric = MetricResult(
        metric="run_error", role=role, score=None, threshold=0.0, passed=None,
        reason=f"{type(err).__name__}: {err}",
    )
    return GoldenOutcome(golden_id=item_id, run=run, metrics=[metric])


async def score_and_append_conversation(cr, conv_run, judge, composer_threshold: float) -> None:
    """Score one conversation run and append its outcomes to a candidate result.

    Adds one per-turn outcome per turn (carrying that turn's real cost) plus one
    conversation-level outcome for the multi-turn metrics. The conversation-level outcome's
    run carries NO cost, so it never double-counts the turns' tokens. Shared by the scripted
    sweep and the simulated sweep.
    """
    conv = conv_run.conversation
    scored = await score_conversation(
        conv_run, judge, per_turn=True, composer_threshold=composer_threshold
    )
    for i, turn_run in enumerate(conv_run.turn_runs):
        cr.outcomes.append(
            GoldenOutcome(golden_id=f"{conv.id}#t{i}", run=turn_run, metrics=scored["per_turn"][i])
        )
    summary = GoldenRun(
        golden_id=conv.id,
        config_label=cr.label,
        input=conv.scenario or conv.id,
        reply=" | ".join(tr.reply for tr in conv_run.turn_runs),
        usage_by_role={},
        costs=[],
        total_cost_usd=None,
    )
    cr.outcomes.append(
        GoldenOutcome(golden_id=conv.id, run=summary, metrics=scored["conversation"])
    )
