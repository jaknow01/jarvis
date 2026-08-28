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

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from evals.harness.config import EvalConfig, JudgeConfig, RoleModel
from evals.harness.cost import PriceTable
from evals.harness.conversation import ScriptedConversation, load_conversations
from evals.harness.datasets import EvalGolden, load_goldens
from evals.harness.report import write_reports
from evals.harness.sweep import CandidateResult, sweep_role

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
    composer_threshold: float = 0.7
    limit: int | None = None
    only_ids: list[str] | None = None
    baseline: dict[str, str] = field(default_factory=dict)

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
            composer_threshold=raw.get("composer_threshold", 0.7),
            limit=raw.get("limit"),
            only_ids=raw.get("only_ids"),
            baseline=raw.get("baseline", {}) or {},
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


async def run_sweep_from_config(cfg: SweepConfig, table: PriceTable | None = None) -> list[CandidateResult]:
    """Execute a sweep from a :class:`SweepConfig` and write JSON + Markdown reports."""
    table = table or PriceTable.load()
    goldens = cfg.select_goldens()
    conversations = cfg.select_conversations()
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
    )
    write_reports(cfg.role, results, judge, run_name=cfg.name)
    return results
