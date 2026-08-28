"""Jarvis evaluation harness (deepeval).

Isolated, non-invasive measurement of the agent network: per-role quality metrics and
per-role token usage / cost, plus a model-sweep engine. Nothing here is imported by
production code under ``lib/`` or ``app/``; the harness only imports *from* them.

Public surface:
- ``UsageCapture`` / ``capture_run`` — per-agent token attribution (own LlamaIndex handlers).
- ``PriceTable`` / ``cost_from_usage`` — token counts -> USD via a configurable table.
- ``EvalConfig`` — a single (role -> model/effort) configuration + env apply/restore.
- ``run_golden`` — drive one golden through the real agents under capture.
- ``build_metrics`` — the reference-free metric set (constant judge).
- ``sweep_role`` — grid a single role over {model, effort}.
- ``write_reports`` — JSON + markdown quality-vs-cost tables.
"""

from evals.harness.instrument import UsageCapture, capture_run, LlmCall
from evals.harness.cost import PriceTable, cost_from_usage, RoleCost
from evals.harness.config import EvalConfig, apply_overrides, JudgeConfig

__all__ = [
    "UsageCapture",
    "capture_run",
    "LlmCall",
    "PriceTable",
    "cost_from_usage",
    "RoleCost",
    "EvalConfig",
    "apply_overrides",
    "JudgeConfig",
]
