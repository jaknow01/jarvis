"""Drive one golden through the real agent network, under token capture.

The driver is deliberately thin: it applies a config's env overrides, runs the *production*
entry point ``engine.handle_message`` (so the coordinator -> sub-agents -> composer path is
exactly what ships), and captures per-agent token usage + cost around it. It does not
re-implement any agent wiring.

Conversation isolation: each golden runs under a unique ``eval:<label>:<gid>:<uuid>``
conversation id, so no prior history is replayed and eval turns are identifiable in the
conversation store.

Principal: turns run as ``EVAL_USER_ID`` (default ``owner``). The roster is opt-in per
principal, so an anonymous turn (``user_id=None``) gets no sub-agents at all.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

import contextlib

from lib.core import Ctx
from lib.runtime.engine import handle_message

from evals.harness.config import EvalConfig, apply_overrides
from evals.harness.cost import PriceTable, RoleCost, cost_from_usage, total_usd
from evals.harness.instrument import UsageCapture, capture_run
from evals.harness.mocks import mock_tools


@dataclass
class GoldenRun:
    """Result of running one golden under one config."""

    golden_id: str
    config_label: str
    input: str
    reply: str
    usage_by_role: dict[str, dict[str, int]]
    costs: list[RoleCost]
    total_cost_usd: float | None
    capture: UsageCapture = field(repr=False, default_factory=UsageCapture)

    def unpriced_models(self) -> list[str]:
        return sorted({c.model or "?" for c in self.costs if c.unpriced})


async def run_golden(
    golden_id: str,
    text: str,
    config: EvalConfig,
    table: PriceTable,
    ctx: Ctx | None = None,
    mock: bool = True,
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> GoldenRun:
    """Run one golden input end-to-end and return reply + per-role usage + cost.

    Args:
        golden_id: stable id of the golden (used in the conversation id and reports).
        text: the user input for this golden.
        config: the (role -> model/effort) overrides to apply for this run.
        table: the price table used to turn token counts into USD.
        ctx: optional shared run context; a fresh ``Ctx()`` is created if omitted.
        mock: when True (default), the agents' tools are replaced with deterministic,
            side-effect-free fixtures — the benchmark measures routing/execution/composition,
            not tool reliability. Set False for a "live" run against the real tools/APIs.
        conversation_id: reuse a fixed conversation id (so prior turns replay as history) —
            used by the multi-turn driver. When omitted, a fresh unique id isolates the run.
        user_id: the principal the turn runs as (roster + per-agent model overrides come
            from its profile). Defaults to ``EVAL_USER_ID`` or ``owner``.
    """
    conversation_id = conversation_id or f"eval:{config.label}:{golden_id}:{uuid.uuid4().hex[:8]}"
    ctx = ctx or Ctx()
    user_id = user_id or os.getenv("EVAL_USER_ID") or "owner"
    tool_ctx = mock_tools() if mock else contextlib.nullcontext()
    with apply_overrides(config):
        with tool_ctx, capture_run() as cap:
            reply = await handle_message(conversation_id, text, ctx, user_id=user_id)

    costs = cost_from_usage(cap.by_role_model(), table)
    return GoldenRun(
        golden_id=golden_id,
        config_label=config.label,
        input=text,
        reply=reply,
        usage_by_role=cap.by_role(),
        costs=costs,
        total_cost_usd=total_usd(costs),
        capture=cap,
    )
