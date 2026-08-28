"""File-based reporting: JSON (raw) + Markdown (quality-vs-cost table) under ``evals/reports``.

No cloud platform and no production database — reporting writes local files only, so the
harness runs fully offline against its own directory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from evals.harness.config import JudgeConfig
from evals.harness.sweep import CandidateResult

_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def _candidate_dict(cr: CandidateResult) -> dict:
    return {
        "role": cr.role,
        "label": cr.label,
        "model": cr.model,
        "reasoning_effort": cr.reasoning_effort,
        "metric_means": cr.metric_means,
        "metric_pass_rate": cr.metric_pass_rate,
        "mean_total_cost_usd": cr.mean_total_cost_usd,
        "mean_role_cost_usd": cr.mean_role_cost_usd,
        "unpriced_models": cr.unpriced_models,
        "n_errors": cr.n_errors,
        "goldens": [
            {
                "golden_id": oc.golden_id,
                "input": oc.run.input,
                "reply": oc.run.reply,
                "usage_by_role": oc.run.usage_by_role,
                "total_cost_usd": oc.run.total_cost_usd,
                "metrics": [
                    {
                        "metric": m.metric,
                        "role": m.role,
                        "score": m.score,
                        "threshold": m.threshold,
                        "passed": m.passed,
                        "reason": m.reason,
                    }
                    for m in oc.metrics
                ],
            }
            for oc in cr.outcomes
        ],
    }


def write_reports(
    role: str,
    candidates: list[CandidateResult],
    judge: JudgeConfig,
    run_name: str | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Write ``<run>.json`` and ``<run>.md`` for a sweep; return the two paths.

    The markdown is the quality-vs-cost comparison table: one row per candidate, metric means
    and pass rates alongside the swept role's mean USD and the mean total USD per turn.
    """
    now = datetime.now(timezone.utc)
    # Group reports by the date they were produced: evals/reports/<YYYY-MM-DD>/<name>.{json,md}
    base = Path(out_dir) if out_dir else _REPORTS_DIR
    out = base / now.strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    run_name = run_name or f"sweep-{role}-{stamp}"

    payload = {
        "run_name": run_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "swept_role": role,
        "judge_model": judge.model,
        "candidates": [_candidate_dict(c) for c in candidates],
    }
    json_path = out / f"{run_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    md_path = out / f"{run_name}.md"
    md_path.write_text(_render_markdown(role, candidates, judge, run_name))
    return {"json": json_path, "markdown": md_path}


def _render_markdown(
    role: str, candidates: list[CandidateResult], judge: JudgeConfig, run_name: str
) -> str:
    # union of metric names across candidates, stable order
    metric_names: list[str] = []
    for c in candidates:
        for m in c.metric_means:
            if m not in metric_names:
                metric_names.append(m)

    lines: list[str] = []
    lines.append(f"# Sweep: `{role}` — quality × cost")
    lines.append("")
    lines.append(f"- Run: `{run_name}`")
    lines.append(f"- Judge (constant): `{judge.model}`")
    lines.append(f"- Candidates: {len(candidates)}")
    lines.append("")

    header = ["candidate", "model", "effort"]
    header += [f"{m} (mean)" for m in metric_names]
    header += [f"{m} (pass%)" for m in metric_names]
    header += ["role $/turn", "total $/turn", "errors", "unpriced"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")

    for c in candidates:
        row = [c.label, f"`{c.model}`", c.reasoning_effort or "—"]
        for m in metric_names:
            v = c.metric_means.get(m)
            row.append(f"{v:.3f}" if v is not None else "—")
        for m in metric_names:
            v = c.metric_pass_rate.get(m)
            row.append(f"{v*100:.0f}%" if v is not None else "—")
        row.append(f"{c.mean_role_cost_usd:.6f}" if c.mean_role_cost_usd is not None else "—")
        row.append(f"{c.mean_total_cost_usd:.6f}" if c.mean_total_cost_usd is not None else "—")
        row.append(str(c.n_errors) if c.n_errors else "—")
        row.append(", ".join(c.unpriced_models) if c.unpriced_models else "—")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append(
        "> Cost is USD, computed from captured per-agent token counts × `price_table.yaml`. "
        "`role $/turn` is the swept role's own mean cost; `total $/turn` is the whole turn "
        "(coordinator + sub-agents + composer). Judge cost is NOT included here (accounted "
        "separately). `unpriced` flags models missing from the price table."
    )
    lines.append("")
    return "\n".join(lines)
