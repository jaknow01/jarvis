"""Unified eval runner — run all agent tests, or just one kind, from one config.

One YAML config defines the experiment (role, candidates, judge, baseline, mock, and which
datasets: goldens / conversations / simulations). ``--only`` picks which workload(s) to run
this invocation:

    python -m evals.run evals/sweeps/composer_example.yaml               # all configured kinds
    python -m evals.run evals/sweeps/composer_example.yaml --only goldens    # single/multi-hop
    python -m evals.run evals/sweeps/composer_example.yaml --only scripted   # deterministic convos
    python -m evals.run evals/sweeps/composer_example.yaml --only simulated  # dynamic convos

Workloads (which data each needs in the config):
    goldens    single/multi-hop questions      -> `dataset:` (e.g. labeled)
    scripted   deterministic conversations     -> `conversations:` (e.g. conversations)
    simulated  dynamic, LLM-driven conversations -> `simulations:` (e.g. simulations)

Every candidate model is compared under identical conditions. Reports are written to a
date-stamped folder: evals/reports/<YYYY-MM-DD>/<name>-<kind>.{json,md}. Goldens + scripted
share one deterministic report; simulated is reported separately (it is non-deterministic
and runs via its own event loop).

Model sweeps are driven the same way for all kinds: the `role` + `candidates` in the config
apply to goldens, scripted, AND simulated. `simulator_model:` (default DEFAULT_MODEL) is the
LLM that role-plays the user in the simulated kind.
"""

import argparse
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from evals.harness.cost import PriceTable  # noqa: E402
from evals.harness.report import _REPORTS_DIR  # noqa: E402
from evals.harness.sweep_config import (  # noqa: E402
    SweepConfig,
    run_simulations_from_config,
    run_sweep_from_config,
)

KINDS = ["all", "goldens", "scripted", "simulated"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Run agent evals from a sweep config.")
    ap.add_argument("config", help="path to a sweep YAML (or a bare name under evals/sweeps/)")
    ap.add_argument("--only", choices=KINDS, default="all",
                    help="which workload to run (default: all configured kinds)")
    args = ap.parse_args()

    cfg = SweepConfig.load(args.config)
    table = PriceTable.load()
    only = args.only

    want_goldens = only in ("all", "goldens")
    want_scripted = only in ("all", "scripted")
    want_simulated = only in ("all", "simulated")

    ran_any = False
    date_dir = f"{_REPORTS_DIR}/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    print(f"Config '{cfg.name}': role={cfg.role}, {len(cfg.candidates)} candidate(s), "
          f"judge={cfg.judge().model}, mock={cfg.mock}")

    # --- deterministic part: goldens and/or scripted conversations (one async sweep) ---
    det_goldens = want_goldens and bool(cfg.dataset)
    det_scripted = want_scripted and bool(cfg.conversations)
    if want_goldens and not cfg.dataset:
        print("  · goldens requested but no `dataset:` in config — skipping goldens")
    if want_scripted and not cfg.conversations:
        print("  · scripted requested but no `conversations:` in config — skipping scripted")

    if det_goldens or det_scripted:
        suffix = "deterministic" if (det_goldens and det_scripted) else ("goldens" if det_goldens else "scripted")
        n_g = len(cfg.select_goldens()) if det_goldens else 0
        n_c = len(cfg.select_conversations()) if det_scripted else 0
        print(f"  → running {n_g} golden(s) + {n_c} scripted conversation(s) …")
        asyncio.run(run_sweep_from_config(
            cfg, table, include_goldens=det_goldens, include_conversations=det_scripted,
            report_name=f"{cfg.name}-{suffix}",
        ))
        print(f"    wrote {date_dir}/{cfg.name}-{suffix}.json + .md")
        ran_any = True

    # --- simulated part: dynamic conversations (sync simulate + async score) ---
    if want_simulated:
        if not cfg.simulations:
            print("  · simulated requested but no `simulations:` in config — skipping simulated")
        else:
            n_s = len(cfg.select_simulations())
            print(f"  → running {n_s} simulated scenario(s) (simulator={cfg.simulator_model or 'DEFAULT_MODEL'}) …")
            run_simulations_from_config(cfg, table)
            print(f"    wrote {date_dir}/{cfg.name}-simulated.json + .md")
            ran_any = True

    if not ran_any:
        print("Nothing ran — check that the config defines the dataset(s) for the requested --only.")


if __name__ == "__main__":
    main()
