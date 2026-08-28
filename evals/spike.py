"""Spike / smoke: prove the capture mechanism on the real network, cheaply (no judge).

Runs one golden through the production ``handle_message`` under :func:`capture_run` and
prints the per-agent token usage, the two-level tool calls (coordinator routing +
sub-agent execution), and the per-role USD cost. This is the mechanism the whole harness
relies on; keep it runnable as a fast health check.

Usage (from the eval venv, with .env in place):
    python -m evals.spike "Jaka jest pogoda w Warszawie i ile kosztuje dolar?"
"""

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from evals.harness.config import EvalConfig  # noqa: E402
from evals.harness.cost import PriceTable  # noqa: E402
from evals.harness.driver import run_golden  # noqa: E402


async def main(text: str) -> None:
    table = PriceTable.load()
    res = await run_golden("spike", text, EvalConfig(label="spike"), table)

    print(f"\nINPUT : {text}")
    print(f"REPLY : {res.reply[:300]}")

    print("\nTOKENS BY ROLE:")
    for role, u in res.usage_by_role.items():
        print(f"  {role:16} prompt={u['prompt_tokens']:6} completion={u['completion_tokens']:5} calls={u['calls']}")

    print("\nTOOL CALLS (two levels):")
    for caller, tools in res.capture.tools_by_caller().items():
        print(f"  {caller:16} -> {tools}")

    print("\nCOST (USD):")
    for c in res.costs:
        tot = "unpriced" if c.unpriced else f"${c.total_cost_usd:.6f}"
        print(f"  {c.role:16} model={c.model} {tot}")
    total = "unpriced" if res.total_cost_usd is None else f"${res.total_cost_usd:.6f}"
    print(f"  {'TOTAL':16} {total}   unpriced_models={res.unpriced_models()}")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "Jaka jest teraz pogoda w Warszawie?"
    asyncio.run(main(query))
