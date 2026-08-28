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

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from deepeval.metrics import ConversationalGEval, KnowledgeRetentionMetric
from deepeval.test_case import ConversationalTestCase, Turn, TurnParams

from lib.core import Ctx

from evals.harness.config import EvalConfig, JudgeConfig
from evals.harness.cost import PriceTable
from evals.harness.datasets import PERSONA
from evals.harness.driver import GoldenRun, run_golden
from evals.harness.metrics import MetricResult, score_golden

_DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"


@dataclass
class ScriptedTurn:
    """One user turn in a scripted conversation, optionally labelled for tool metrics."""

    input: str
    expected_routing: list[str] | None = None
    expected_execution: dict[str, list[str]] | None = None

    def to_dict(self) -> dict:
        d: dict = {"input": self.input}
        if self.expected_routing is not None:
            d["expected_routing"] = self.expected_routing
        if self.expected_execution is not None:
            d["expected_execution"] = self.expected_execution
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ScriptedTurn":
        return cls(
            input=d["input"],
            expected_routing=d.get("expected_routing"),
            expected_execution=d.get("expected_execution"),
        )


@dataclass
class ScriptedConversation:
    """A fixed, ordered sequence of user turns forming one benchmark conversation."""

    id: str
    turns: list[ScriptedTurn]
    scenario: str = ""
    persona: str = PERSONA

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "scenario": self.scenario,
            "persona": self.persona,
            "turns": [t.to_dict() for t in self.turns],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScriptedConversation":
        return cls(
            id=d["id"],
            scenario=d.get("scenario", ""),
            persona=d.get("persona", PERSONA),
            turns=[ScriptedTurn.from_dict(t) for t in d["turns"]],
        )


@dataclass
class SimScenario:
    """A scenario for the DYNAMIC layer: no fixed turns — the simulated user improvises them.

    ``scenario`` describes what the owner wants; ``expected_outcome`` is the success
    condition the simulator drives toward. Serialisable to JSON like the scripted set.
    """

    id: str
    scenario: str
    expected_outcome: str
    persona: str = PERSONA

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "scenario": self.scenario,
            "expected_outcome": self.expected_outcome,
            "persona": self.persona,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SimScenario":
        return cls(
            id=d["id"],
            scenario=d["scenario"],
            expected_outcome=d["expected_outcome"],
            persona=d.get("persona", PERSONA),
        )


# --------------------------------------------------------------- JSON persistence
def save_conversations(conversations: list[ScriptedConversation], name: str = "conversations") -> Path:
    """Persist scripted conversations to ``evals/datasets/<name>.json``."""
    _DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    path = _DATASETS_DIR / f"{name}.json"
    path.write_text(json.dumps([c.to_dict() for c in conversations], ensure_ascii=False, indent=2))
    return path


def load_conversations(name: str = "conversations") -> list[ScriptedConversation]:
    """Load scripted conversations from ``evals/datasets/<name>.json``."""
    path = _DATASETS_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    return [ScriptedConversation.from_dict(d) for d in data]


def save_sim_scenarios(scenarios: list[SimScenario], name: str = "simulations") -> Path:
    """Persist simulation scenarios to ``evals/datasets/<name>.json``."""
    _DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    path = _DATASETS_DIR / f"{name}.json"
    path.write_text(json.dumps([s.to_dict() for s in scenarios], ensure_ascii=False, indent=2))
    return path


def load_sim_scenarios(name: str = "simulations") -> list[SimScenario]:
    """Load simulation scenarios from ``evals/datasets/<name>.json``."""
    path = _DATASETS_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    return [SimScenario.from_dict(d) for d in data]


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


def build_conversation_geval(judge_model, threshold: float = 0.7) -> ConversationalGEval:
    """Polish multi-turn quality: role adherence + cross-turn context consistency.
    ``judge_model`` is a resolved deepeval model (string or adapter)."""
    return ConversationalGEval(
        name="conversation_quality_pl",
        model=judge_model,
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

    judge_model = judge.deepeval_model()  # resolve once for the conversation-level metrics
    conv_geval = build_conversation_geval(judge_model)
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
        kr = KnowledgeRetentionMetric(model=judge_model)
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
