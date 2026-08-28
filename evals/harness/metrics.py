"""Reference-free metric set, scored by one constant judge.

Design constraints (see design.md / spec eval-harness):
- The dataset is synthetic (deepeval ``Synthesizer``) with no ground-truth labels, so the
  default metrics need no ``expected_output`` / ``expected_tools``.
- A single fixed judge (``JudgeConfig``) scores every metric across a whole sweep so
  candidates are comparable.

Metrics built here:
- ``composer_quality`` — a Polish-language ``GEval`` on the final reply: relevance to the
  user's message, faithfulness to the data the sub-agents gathered, absence of invented
  content, and natural Polish assistant tone. Scored on an ``LLMTestCase`` (works offline).
- ``routing_correctness`` / ``execution_correctness`` — OPTIONAL ``ToolCorrectnessMetric``
  applied only when a golden carries ``expected_tools`` for that level, using the tool calls
  our own capture recorded (coordinator's sub-agent picks = routing; a sub-agent's real
  tools = execution). Absent labels => the metric is simply skipped, never a failure.

Component-level LLM-judged quality per sub-agent (via deepeval span contexts) is a future
enhancement: deepeval only retains spans during its own evaluation flow, whereas token/cost
capture here is independent. The tool-level metrics above already give a per-role signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from deepeval.metrics import GEval, ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase, LLMTestCaseParams, ToolCall

from evals.harness.config import JudgeConfig
from evals.harness.driver import GoldenRun


@dataclass
class MetricResult:
    """One metric's outcome for one golden."""

    metric: str
    role: str
    score: float | None
    threshold: float
    passed: bool | None
    reason: str | None


def build_composer_quality(judge_model, threshold: float = 0.7) -> GEval:
    """Polish-language answer quality on the final composer reply.

    CONTEXT carries the coordinator's gathered data, so the judge scores *faithfulness to
    what the sub-agents actually returned* rather than penalising every specific number as
    if it were unfounded. ``judge_model`` is a resolved deepeval model (string or adapter).
    """
    return GEval(
        name="composer_quality_pl",
        model=judge_model,
        threshold=threshold,
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.CONTEXT,
        ],
        evaluation_steps=[
            "Oceń, czy odpowiedź (ACTUAL_OUTPUT) trafnie i wprost odnosi się do wiadomości "
            "użytkownika (INPUT).",
            "Sprawdź wierność: każdy fakt, liczba, jednostka i data w ACTUAL_OUTPUT musi "
            "wynikać z danych zebranych przez podagentów (CONTEXT). Karz treści sprzeczne z "
            "CONTEXT lub w nim nieobecne (halucynacje); nie karz wartości, które są w CONTEXT.",
            "Sprawdź, czy odpowiedź jest po polsku, zwięzła i w naturalnym, asystenckim tonie.",
            "Karz odpowiedzi, które ujawniają wewnętrzne agenty/narzędzia lub brzmią jak surowy "
            "zrzut danych zamiast gotowej wiadomości do użytkownika.",
        ],
    )


def _tool_calls_for(run: GoldenRun, role: str) -> list[ToolCall]:
    """The tools a given role invoked during the run, as deepeval ToolCall objects."""
    names = run.capture.tools_by_caller().get(role, [])
    return [ToolCall(name=n) for n in names]


async def score_golden(
    run: GoldenRun,
    judge: JudgeConfig,
    expected_routing: list[str] | None = None,
    expected_execution: dict[str, list[str]] | None = None,
    composer_threshold: float = 0.7,
) -> list[MetricResult]:
    """Score one completed :class:`GoldenRun` with the reference-free metric set.

    Async on purpose: deepeval metrics expose ``a_measure`` which we ``await`` directly, so
    scoring runs inside the harness's event loop without ``nest_asyncio`` re-entrancy (that
    combination breaks under Python 3.14's asyncio).

    Args:
        run: the completed golden run (final reply + captured tool calls).
        judge: the constant judge model.
        expected_routing: OPTIONAL list of sub-agent names the coordinator *should* have
            called; when given, adds a routing ToolCorrectness metric.
        expected_execution: OPTIONAL ``{subagent: [expected tool names]}``; when given, adds
            an execution ToolCorrectness metric per listed sub-agent.
        composer_threshold: pass threshold for the composer-quality GEval.
    """
    results: list[MetricResult] = []
    judge_model = judge.deepeval_model()  # resolve once; reuse for every metric in this call

    # 1) Composer answer quality (always, reference-free). The coordinator's gathered data
    #    is passed as CONTEXT so faithfulness is judged against what the sub-agents returned.
    geval = build_composer_quality(judge_model, threshold=composer_threshold)
    gathered = run.capture.agent_outputs.get("coordinator", "")
    tc = LLMTestCase(
        input=run.input,
        actual_output=run.reply,
        context=[gathered] if gathered else ["(brak zebranych danych)"],
    )
    await geval.a_measure(tc)
    results.append(
        MetricResult(
            metric="composer_quality_pl",
            role="composer",
            score=geval.score,
            threshold=geval.threshold,
            passed=(geval.score is not None and geval.score >= geval.threshold),
            reason=geval.reason,
        )
    )

    # 2) Routing correctness (optional, only with labels).
    if expected_routing is not None:
        results.append(
            await _tool_correctness(
                metric_name="routing_correctness",
                role="coordinator",
                tools_called=_tool_calls_for(run, "coordinator"),
                expected=[ToolCall(name=n) for n in expected_routing],
                actual_output=run.reply,
                user_input=run.input,
                judge_model=judge_model,
            )
        )

    # 3) Execution correctness per sub-agent (optional, only with labels).
    for subagent, expected_tools in (expected_execution or {}).items():
        results.append(
            await _tool_correctness(
                metric_name=f"execution_correctness[{subagent}]",
                role=subagent,
                tools_called=_tool_calls_for(run, subagent),
                expected=[ToolCall(name=n) for n in expected_tools],
                actual_output=run.reply,
                user_input=run.input,
                judge_model=judge_model,
            )
        )

    return results


async def _tool_correctness(
    metric_name: str,
    role: str,
    tools_called: list[ToolCall],
    expected: list[ToolCall],
    actual_output: str,
    user_input: str,
    judge_model,
) -> MetricResult:
    metric = ToolCorrectnessMetric(model=judge_model)
    tc = LLMTestCase(
        input=user_input,
        actual_output=actual_output,
        tools_called=tools_called,
        expected_tools=expected,
    )
    await metric.a_measure(tc)
    return MetricResult(
        metric=metric_name,
        role=role,
        score=metric.score,
        threshold=metric.threshold,
        passed=(metric.score is not None and metric.score >= metric.threshold),
        reason=getattr(metric, "reason", None),
    )
