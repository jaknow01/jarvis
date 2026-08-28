"""Run a sweep from a declarative YAML config.

Usage (from the eval venv, with .env in place):
    python -m evals.scripts.run_sweep evals/sweeps/composer_example.yaml
    python -m evals.scripts.run_sweep composer_example        # bare name -> evals/sweeps/<name>.yaml

The config defines the swept role, candidate models, baseline, dataset, judge, mock, and
optional subset (`limit` / `only_ids`). Results are written to evals/reports/<name>.{json,md}.
See evals/harness/sweep_config.py for the schema.
"""

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from evals.harness.cost import PriceTable  # noqa: E402
from evals.harness.sweep_config import SweepConfig, run_sweep_from_config  # noqa: E402
from evals.harness.report import _REPORTS_DIR  # noqa: E402


async def main(argv: list[str]) -> None:
    if not argv:
        print("usage: python -m evals.scripts.run_sweep <config.yaml | name>")
        return

    cfg = SweepConfig.load(argv[0])
    goldens = cfg.select_goldens()
    conversations = cfg.select_conversations()
    print(f"Sweep '{cfg.name}': role={cfg.role}, {len(cfg.candidates)} candidate(s) on "
          f"{len(goldens)} golden(s) + {len(conversations)} conversation(s) (mock={cfg.mock})")
    print(f"Judge (constant): {cfg.judge().model}")

    await run_sweep_from_config(cfg, table=PriceTable.load())
    print(f"\nWrote reports to {_REPORTS_DIR}/{cfg.name}.json and .md")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
