"""Declarative sweep config — a YAML experiment definition, run reproducibly.

A sweep is fully described by a small YAML file (see ``evals/sweeps/*.yaml``): which role to
sweep, the candidate models/efforts, the fixed baseline for the other roles, which dataset,
the constant judge, mock vs live, thresholds, and an optional subset for cheap runs. This
keeps experiments in data (easy to review/modify/diff) rather than in code.

Schema::

    name: composer-comparison        # report/run name
    role: composer                   # the role being swept
    judge_model: gpt-4o              # constant judge (bare = OpenAI; provider/model = adapter)
    mock: true                       # deterministic, side-effect-free tools (default true)
    dataset: labeled                 # goldens file under evals/datasets/ (default "labeled")
    composer_threshold: 0.7          # pass threshold for composer_quality_pl
    limit: 6                         # OPTIONAL: cap number of goldens (cheap runs)
    only_ids: [sh_weather_now, ...]  # OPTIONAL: run only these golden ids
    baseline:                        # OPTIONAL: fixed models for the OTHER roles
      coordinator: openai/gpt-5.6-luna
    candidates:                      # the grid for the swept role
      - model: openai/gpt-4.1-mini
      - model: openai/gpt-5.6-luna
        reasoning_effort: low
      - model: openrouter/deepseek/deepseek-v4-flash-0731
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from evals.harness.config import EvalConfig, JudgeConfig, RoleModel
from evals.harness.cost import PriceTable
from evals.harness.conversation import (
    ScriptedConversation,
    SimScenario,
    load_conversations,
    load_sim_scenarios,
)
from evals.harness.datasets import EvalGolden, load_goldens
from evals.harness.report import write_reports
from evals.harness.sweep import CandidateResult, score_and_append_conversation, sweep_role

_SWEEPS_DIR = Path(__file__).resolve().parent.parent / "sweeps"


@dataclass
class SweepConfig:
    """Parsed sweep experiment definition."""

    name: str
    role: str
    candidates: list[RoleModel]
    judge_model: str | None = None
    mock: bool = True
    dataset: str | None = "labeled"   # goldens file (single/multi-hop); None => no goldens
    conversations: str | None = None  # scripted conversations file (multi-turn); None => none
    simulations: str | None = None    # simulation scenarios file (dynamic); None => none
    simulator_model: str | None = None  # LLM that role-plays the user (default: DEFAULT_MODEL)
    max_turns: int = 6                # cap on simulated user turns
    composer_threshold: float = 0.7
    limit: int | None = None
    only_ids: list[str] | None = None
    baseline: dict[str, str] = field(default_factory=dict)
    timeout: float = 180.0            # per-item wall-clock cap (s); a hang becomes a run_error

    @classmethod
    def load(cls, path: str | Path) -> "SweepConfig":
        """Load a sweep config from a YAML path (absolute, or a name under evals/sweeps/)."""
        p = Path(path)
        if not p.exists() and not p.is_absolute():
            # allow bare names: "composer" -> evals/sweeps/composer.yaml
            cand = _SWEEPS_DIR / (p.name if p.suffix else f"{p.name}.yaml")
            if cand.exists():
                p = cand
        raw = yaml.safe_load(p.read_text())
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "SweepConfig":
        role = raw["role"]
        candidates = []
        for c in raw["candidates"]:
            if isinstance(c, str):
                candidates.append(RoleModel(role=role, model=c))
            else:
                candidates.append(
                    RoleModel(role=role, model=c["model"], reasoning_effort=c.get("reasoning_effort"))
                )
        # goldens dataset: `goldens` (preferred) or `dataset` (back-compat). If neither the
        # goldens nor conversations are given, default to the labeled goldens set.
        conversations = raw.get("conversations")
        dataset = raw.get("goldens", raw.get("dataset", "__unset__"))
        if dataset == "__unset__":
            dataset = None if conversations else "labeled"
        return cls(
            name=raw.get("name", f"sweep-{role}"),
            role=role,
            candidates=candidates,
            judge_model=raw.get("judge_model"),
            mock=raw.get("mock", True),
            dataset=dataset,
            conversations=conversations,
            simulations=raw.get("simulations"),
            simulator_model=raw.get("simulator_model"),
            max_turns=raw.get("max_turns", 6),
            composer_threshold=raw.get("composer_threshold", 0.7),
            limit=raw.get("limit"),
            only_ids=raw.get("only_ids"),
            baseline=raw.get("baseline", {}) or {},
            timeout=raw.get("timeout", 180.0),
        )

    # ------------------------------------------------------------------ derived
    def judge(self) -> JudgeConfig:
        return JudgeConfig(model=self.judge_model) if self.judge_model else JudgeConfig()

    def baseline_config(self) -> EvalConfig:
        overrides = {r: RoleModel(role=r, model=m) for r, m in self.baseline.items()}
        return EvalConfig(label=f"{self.name}:baseline", overrides=overrides)

    def select_goldens(self) -> list[EvalGolden]:
        if not self.dataset:
            return []
        goldens = load_goldens(self.dataset)
        if self.only_ids:
            wanted = set(self.only_ids)
            goldens = [g for g in goldens if g.id in wanted]
        if self.limit is not None:
            goldens = goldens[: self.limit]
        return goldens

    def select_conversations(self) -> list[ScriptedConversation]:
        return load_conversations(self.conversations) if self.conversations else []

    def select_simulations(self) -> list[SimScenario]:
        return load_sim_scenarios(self.simulations) if self.simulations else []

    def candidate_config(self, cand: RoleModel) -> tuple[str, EvalConfig]:
        """Return ``(label, EvalConfig)`` for one candidate of the swept role (baseline +
        the candidate's override). Mirrors what ``sweep_role`` builds internally."""
        cand = RoleModel(role=self.role, model=cand.model, reasoning_effort=cand.reasoning_effort)
        label = f"{self.role}={cand.model}" + (f"@{cand.reasoning_effort}" if cand.reasoning_effort else "")
        overrides = dict(self.baseline_config().overrides)
        overrides[self.role] = cand
        return label, EvalConfig(label=label, overrides=overrides)


async def run_sweep_from_config(
    cfg: SweepConfig,
    table: PriceTable | None = None,
    include_goldens: bool = True,
    include_conversations: bool = True,
    report_name: str | None = None,
) -> list[CandidateResult]:
    """Execute the DETERMINISTIC sweep (goldens and/or scripted conversations) and write
    JSON + Markdown reports. ``include_*`` narrow which workloads run this invocation."""
    table = table or PriceTable.load()
    goldens = cfg.select_goldens() if include_goldens else []
    conversations = cfg.select_conversations() if include_conversations else []
    judge = cfg.judge()
    results = await sweep_role(
        role=cfg.role,
        candidates=cfg.candidates,
        goldens=goldens,
        judge=judge,
        table=table,
        baseline=cfg.baseline_config(),
        composer_threshold=cfg.composer_threshold,
        mock=cfg.mock,
        conversations=conversations,
        item_timeout=cfg.timeout,
    )
    write_reports(cfg.role, results, judge, run_name=report_name or cfg.name)
    return results


def run_simulations_from_config(
    cfg: SweepConfig, table: PriceTable | None = None, report_name: str | None = None
) -> list[CandidateResult]:
    """Sweep the swept role over the DYNAMIC (simulated) scenarios.

    Two phases, on purpose: the ``ConversationSimulator`` runs its own event loop, so the
    simulate calls happen synchronously at top level (phase 1), and scoring — which is async —
    runs afterwards in a single ``asyncio.run`` (phase 2). This avoids nested event loops
    (``nest_asyncio`` breaks under Python 3.14). Writes a ``<name>-simulated`` report.
    """
    from evals.harness.simulator import golden_from_sim_scenario, simulate_conversation

    table = table or PriceTable.load()
    scenarios = cfg.select_simulations()
    sim_model = cfg.simulator_model or os.getenv("DEFAULT_MODEL")
    judge = cfg.judge()

    # phase 1 (sync): drive the simulator per candidate x scenario, collecting ConversationRuns
    collected: list[tuple[RoleModel, list]] = []
    for cand in cfg.candidates:
        label, config = cfg.candidate_config(cand)
        runs = []
        for scen in scenarios:
            golden = golden_from_sim_scenario(scen)
            crun = simulate_conversation(
                golden, config, table, simulator_model=sim_model,
                mock=cfg.mock, max_turns=cfg.max_turns,
            )
            crun.conversation.id = scen.id  # label the conversation by its scenario
            runs.append(crun)
        collected.append((cand, runs))

    # phase 2 (async): score every collected conversation and aggregate per candidate
    results = asyncio.run(_score_simulated(cfg, collected, judge))
    write_reports(cfg.role, results, judge, run_name=report_name or f"{cfg.name}-simulated")
    return results


async def _score_simulated(cfg: SweepConfig, collected: list, judge: JudgeConfig) -> list[CandidateResult]:
    results: list[CandidateResult] = []
    for cand, runs in collected:
        label, _ = cfg.candidate_config(cand)
        cr = CandidateResult(
            role=cfg.role, label=label, model=cand.model, reasoning_effort=cand.reasoning_effort
        )
        for crun in runs:
            await score_and_append_conversation(cr, crun, judge, cfg.composer_threshold)
        results.append(cr.finalize())
    return results
