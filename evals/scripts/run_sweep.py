"""Example sweep runner: grid one role over candidate models, write a quality×cost report.

This is a thin CLI over :func:`evals.harness.sweep.sweep_role` + :func:`write_reports`.
It runs the seed dataset (or a named one) for a role over a small candidate list. Edit the
CANDIDATES below (or pass models on the command line) to compare your own options.

Usage (from the eval venv, with .env in place):
    python -m evals.scripts.run_sweep composer openai/gpt-4.1-mini openai/gpt-5.6-luna
    python -m evals.scripts.run_sweep weather_agent openai/gpt-4.1-mini --dataset seed

Notes:
- The judge is constant across the sweep (EVAL_JUDGE_MODEL, default in JudgeConfig).
- Cost uses evals/price_table.yaml; unknown models are flagged "unpriced" (not zero).
- Every candidate is evaluated on the SAME goldens, for a fair comparison.
"""

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from evals.harness.config import JudgeConfig, RoleModel  # noqa: E402
from evals.harness.cost import PriceTable  # noqa: E402
from evals.harness.datasets import ensure_seed, load_goldens  # noqa: E402
from evals.harness.report import write_reports  # noqa: E402
from evals.harness.sweep import sweep_role  # noqa: E402


async def main(argv: list[str]) -> None:
    if not argv:
        print("usage: python -m evals.scripts.run_sweep <role> [model ...] [--dataset NAME]")
        return

    role = argv[0]
    rest = argv[1:]
    dataset = "seed"
    if "--dataset" in rest:
        i = rest.index("--dataset")
        dataset = rest[i + 1]
        rest = rest[:i] + rest[i + 2 :]

    models = rest or ["openai/gpt-4.1-mini"]
    candidates = [RoleModel(role=role, model=m) for m in models]

    goldens = load_goldens(dataset) if dataset != "seed" else ensure_seed()
    judge = JudgeConfig()
    table = PriceTable.load()

    print(f"Sweeping role={role} over {len(candidates)} candidate(s) on {len(goldens)} golden(s)")
    print(f"Judge (constant): {judge.model}")
    results = await sweep_role(role, candidates, goldens, judge, table)

    paths = write_reports(role, results, judge)
    print(f"\nWrote:\n  {paths['json']}\n  {paths['markdown']}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
