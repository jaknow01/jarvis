"""Scripted multi-turn conversations — the deterministic multi-turn benchmark.

A scripted conversation is a fixed sequence of user turns run through the SAME
``conversation_id``, so the app replays accumulating history each turn (continuity is owned
by ``engine`` in Postgres/JSON). Tools are mocked by default, so the whole conversation is
deterministic and side-effect free — the ideal fair benchmark for comparing models.

Two layers of scoring:
- **Per-turn** — reuse the single-turn metric set (``composer_quality_pl`` + optional
  routing/execution correctness) on each turn's :class:`GoldenRun`.
- **Conversation-level** — deepeval multi-turn metrics over the assembled turns:
  ``ConversationalGEval`` (stays in the Polish assistant role, keeps context across turns)
  and ``KnowledgeRetentionMetric`` (remembers facts the user gave earlier — directly
  relevant to the ``memory_operator``).

Per-agent token/cost capture works per turn and is summed to a conversation total.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from deepeval.metrics import ConversationalGEval, KnowledgeRetentionMetric
from deepeval.test_case import ConversationalTestCase, Turn, TurnParams

from lib.core import Ctx

from evals.harness.config import EvalConfig, JudgeConfig
from evals.harness.cost import PriceTable
from evals.harness.datasets import PERSONA
from evals.harness.driver import GoldenRun, run_golden
from evals.harness.metrics import MetricResult, score_golden


@dataclass
class ScriptedTurn:
    """One user turn in a scripted conversation, optionally labelled for tool metrics."""

    input: str
    expected_routing: list[str] | None = None
    expected_execution: dict[str, list[str]] | None = None


@dataclass
class ScriptedConversation:
    """A fixed, ordered sequence of user turns forming one benchmark conversation."""

    id: str
    turns: list[ScriptedTurn]
    scenario: str = ""
    persona: str = PERSONA


@dataclass
class ConversationRun:
    """Result of running one scripted conversation (all turns share one conversation id)."""

    conversation: ScriptedConversation
    config_label: str
    turn_runs: list[GoldenRun] = field(default_factory=list)

    def deepeval_turns(self) -> list[Turn]:
        """Assemble the user/assistant turns for a deepeval ConversationalTestCase."""
        turns: list[Turn] = []
        for tr in self.turn_runs:
            turns.append(Turn(role="user", content=tr.input))
            turns.append(Turn(role="assistant", content=tr.reply))
        return turns

    @property
    def total_cost_usd(self) -> float | None:
        totals = [tr.total_cost_usd for tr in self.turn_runs if tr.total_cost_usd is not None]
        return round(sum(totals), 6) if totals else None


async def run_conversation(
    conversation: ScriptedConversation,
    config: EvalConfig,
    table: PriceTable,
    mock: bool = True,
) -> ConversationRun:
    """Run every turn of ``conversation`` under one conversation id (history accumulates)."""
    conv_id = f"eval:conv:{conversation.id}:{config.label}:{uuid.uuid4().hex[:8]}"
    ctx = Ctx()  # one shared context for the whole conversation
    run = ConversationRun(conversation=conversation, config_label=config.label)
    for i, turn in enumerate(conversation.turns):
        gr = await run_golden(
            golden_id=f"{conversation.id}#t{i}",
            text=turn.input,
            config=config,
            table=table,
            ctx=ctx,
            mock=mock,
            conversation_id=conv_id,
        )
        run.turn_runs.append(gr)
    return run


def build_conversation_geval(judge: JudgeConfig, threshold: float = 0.7) -> ConversationalGEval:
    """Polish multi-turn quality: role adherence + cross-turn context consistency."""
    return ConversationalGEval(
        name="conversation_quality_pl",
        model=judge.model,
        threshold=threshold,
        evaluation_params=[TurnParams.ROLE, TurnParams.CONTENT],
        evaluation_steps=[
            "Oceń, czy asystent utrzymuje spójny kontekst między turami — wykorzystuje to, co "
            "użytkownik powiedział wcześniej, i nie zapomina wcześniejszych ustaleń.",
            "Sprawdź, czy każda odpowiedź jest po polsku, w naturalnym tonie asystenta, i nie "
            "ujawnia wewnętrznych agentów/narzędzi.",
            "Karz sprzeczności między turami oraz odpowiedzi ignorujące wcześniejszą prośbę.",
        ],
    )


async def score_conversation(
    run: ConversationRun,
    judge: JudgeConfig,
    per_turn: bool = True,
    knowledge_retention: bool = True,
    composer_threshold: float = 0.7,
) -> dict:
    """Score a conversation: per-turn single-turn metrics + conversation-level metrics.

    Returns ``{"per_turn": [[MetricResult,...] per turn], "conversation": [MetricResult,...]}``.
    """
    result: dict = {"per_turn": [], "conversation": []}

    # per-turn reuse of the single-turn metric set
    if per_turn:
        for i, gr in enumerate(run.turn_runs):
            turn = run.conversation.turns[i]
            metrics = await score_golden(
                gr,
                judge,
                expected_routing=turn.expected_routing,
                expected_execution=turn.expected_execution,
                composer_threshold=composer_threshold,
            )
            result["per_turn"].append(metrics)

    # conversation-level metrics over the assembled turns
    tc = ConversationalTestCase(
        turns=run.deepeval_turns(),
        scenario=run.conversation.scenario or None,
        user_description=run.conversation.persona,
    )

    conv_geval = build_conversation_geval(judge)
    await conv_geval.a_measure(tc)
    result["conversation"].append(
        MetricResult(
            metric="conversation_quality_pl",
            role="assistant",
            score=conv_geval.score,
            threshold=conv_geval.threshold,
            passed=(conv_geval.score is not None and conv_geval.score >= conv_geval.threshold),
            reason=conv_geval.reason,
        )
    )

    if knowledge_retention:
        kr = KnowledgeRetentionMetric(model=judge.model)
        await kr.a_measure(tc)
        result["conversation"].append(
            MetricResult(
                metric="knowledge_retention",
                role="assistant",
                score=kr.score,
                threshold=kr.threshold,
                passed=(kr.score is not None and kr.score >= kr.threshold),
                reason=getattr(kr, "reason", None),
            )
        )

    return result
