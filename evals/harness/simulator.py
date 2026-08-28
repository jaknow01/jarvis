"""Simulated conversations — the complementary, dynamic multi-turn layer.

deepeval's ``ConversationSimulator`` role-plays the owner (an LLM driven by a persona +
scenario) and talks to our assistant until the expected outcome is reached. Unlike the
scripted benchmark, the turns are generated on the fly, so it probes how a model copes with
an unscripted flow — a different signal, not a 1:1 comparison.

Integration points:
- The simulator calls our ``model_callback`` per assistant turn. deepeval passes ``input``
  and a per-conversation ``thread_id``; we key each conversation's state (fixed
  ``conversation_id`` + shared ``Ctx`` + accumulated ``GoldenRun``s) on that thread id, so
  the app replays history and our capture/cost machinery runs per turn (via ``run_golden``).
- The simulated user LLM uses our provider-agnostic adapter, so it can be a strong model
  (e.g. deepseek via OpenRouter), not only OpenAI.
- ``max_concurrent=1``: the tool mock and the dispatcher capture are process-global, so
  conversations are run sequentially to keep per-turn attribution and mocking clean.

The result is converted into the same :class:`ConversationRun` the scripted path produces, so
``score_conversation`` scores it identically (per-turn + conversation-level metrics).
"""

from __future__ import annotations

import json
import uuid

from deepeval.dataset import ConversationalGolden
from deepeval.simulator import ConversationSimulator
from deepeval.test_case import Turn

from lib.core import Ctx

from evals.harness.config import EvalConfig, JudgeConfig
from evals.harness.cost import PriceTable
from evals.harness.conversation import (
    ScriptedConversation,
    ScriptedTurn,
    ConversationRun,
    SimScenario,
)
from evals.harness.datasets import PERSONA
from evals.harness.deepeval_model import deepeval_model_for
from evals.harness.driver import run_golden


def _unwrap_simulated_input(text: str) -> str:
    """Defensive: with a custom simulator model, deepeval occasionally passes the opening
    user turn as the raw structured object ``{"simulated_input": "..."}`` instead of the
    plain string. Unwrap it so the assistant (and the metrics) see clean user text.
    """
    t = text.strip()
    if t.startswith("{") and "simulated_input" in t:
        try:
            return json.loads(t).get("simulated_input", text)
        except (ValueError, TypeError):
            return text
    return text


def make_conversational_golden(
    scenario: str,
    expected_outcome: str,
    persona: str = PERSONA,
) -> ConversationalGolden:
    """Build a deepeval ConversationalGolden for the owner persona."""
    return ConversationalGolden(
        scenario=scenario,
        expected_outcome=expected_outcome,
        user_description=persona,
    )


def golden_from_sim_scenario(sim: SimScenario) -> ConversationalGolden:
    """Build a deepeval ConversationalGolden from a JSON-loaded :class:`SimScenario`."""
    return make_conversational_golden(sim.scenario, sim.expected_outcome, sim.persona)


def simulate_conversation(
    golden: ConversationalGolden,
    config: EvalConfig,
    table: PriceTable,
    simulator_model: str,
    mock: bool = True,
    max_turns: int = 6,
) -> ConversationRun:
    """Run ONE simulated conversation and return it as a :class:`ConversationRun`.

    Synchronous on purpose: ``ConversationSimulator.simulate`` manages its own event loop and
    awaits our async callback, so it must be called from a sync context (not inside
    ``asyncio.run``). Score the returned run separately with ``score_conversation``.

    Args:
        golden: the scenario/expected-outcome/persona the simulated user pursues.
        config: role -> model/effort overrides for the assistant under test.
        table: price table for per-turn cost.
        simulator_model: the model that role-plays the user (``provider/model`` string;
            wrapped via the deepeval adapter so any provider works).
        mock: mock the assistant's tools (default) vs run live.
        max_turns: cap on simulated user turns.
    """
    conv_id = f"eval:sim:{golden.scenario[:12]}:{uuid.uuid4().hex[:8]}"
    ctx = Ctx()
    turn_runs: list = []

    async def model_callback(input: str, thread_id: str) -> Turn:
        gr = await run_golden(
            golden_id=f"sim#{len(turn_runs)}",
            text=_unwrap_simulated_input(input),
            config=config,
            table=table,
            ctx=ctx,
            mock=mock,
            conversation_id=conv_id,
        )
        turn_runs.append(gr)
        return Turn(role="assistant", content=gr.reply)

    simulator = ConversationSimulator(
        model_callback=model_callback,
        simulator_model=deepeval_model_for(simulator_model),
        language="Polish",
        max_concurrent=1,  # global tool-mock + dispatcher capture -> run sequentially
    )
    simulator.simulate([golden], max_user_simulations=max_turns)

    # Convert to the shared ConversationRun so score_conversation handles it uniformly.
    scripted = ScriptedConversation(
        id=f"sim_{uuid.uuid4().hex[:6]}",
        turns=[ScriptedTurn(input=gr.input) for gr in turn_runs],
        scenario=golden.scenario or "",
        persona=golden.user_description or PERSONA,
    )
    run = ConversationRun(conversation=scripted, config_label=config.label)
    run.turn_runs = turn_runs
    return run
