"""Non-invasive per-agent token capture over the LlamaIndex dispatcher.

Why our own handlers (and not deepeval's trace store): deepeval flushes and clears its
traces after each run when there is no Confident AI key, so ``trace_manager.get_all_traces()``
returns nothing offline (verified in the spike). Token/cost attribution therefore rides on
our own LlamaIndex event + span handlers, which we fully control and which work for every
provider. deepeval's ``instrument_llama_index`` is still enabled here so the *metrics* path
(which retains spans during its own evaluation flow) keeps working.

Attribution mechanism (verified in the spike):
- Every agent in the app is a ``FunctionAgent`` built with ``name=<role>`` (coordinator,
  composer, weather_agent, ...). Its ``FunctionAgent.run`` span carries that instance, so we
  record ``span_id -> role`` for those spans and a ``span_id -> parent`` map for all spans.
- ``LLMChatEndEvent`` carries ``span_id`` and ``response.additional_kwargs`` with
  ``prompt_tokens`` / ``completion_tokens``. We walk the parent chain from the LLM span to
  the nearest recorded agent span => the role that produced those tokens. A sub-agent run
  nested inside the coordinator's tool call resolves to the sub-agent (nearest wins).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from llama_index.core.instrumentation import get_dispatcher
from llama_index.core.instrumentation.event_handlers import BaseEventHandler
from llama_index.core.instrumentation.span_handlers import BaseSpanHandler
from llama_index.core.agent.workflow import FunctionAgent

# Role used when an LLM call cannot be tied to any named agent span (should be rare).
UNATTRIBUTED = "_unattributed"


@dataclass
class LlmCall:
    """One LLM completion attributed to a role, with token counts and the model used."""

    role: str
    model: str | None
    provider: str | None
    prompt_tokens: int
    completion_tokens: int


@dataclass
class ToolInvocation:
    """One tool call, attributed to the agent (role) that made it.

    For the coordinator, ``tool`` is a sub-agent name (routing decision, level 1). For a
    sub-agent, ``tool`` is one of its real tools, e.g. ``get_route_details`` (execution
    decision, level 2). ``caller`` is the nearest enclosing named agent.
    """

    caller: str
    tool: str
    tool_input: str | None = None


@dataclass
class UsageCapture:
    """Accumulates per-agent LLM calls seen during one or more runs.

    ``calls`` is the raw list; ``by_role()`` and ``by_role_model()`` aggregate it. The same
    capture can span a whole turn (coordinator + sub-agents + composer) because attribution
    is per-LLM-call, not per-run.
    """

    calls: list[LlmCall] = field(default_factory=list)
    tools: list[ToolInvocation] = field(default_factory=list)
    # role -> the agent's final output string (last run wins). The coordinator's output is
    # the "gathered data" the composer must stay faithful to (used as judge CONTEXT).
    agent_outputs: dict[str, str] = field(default_factory=dict)
    # internal span bookkeeping (span_id -> parent, span_id -> role, span_id -> model/provider)
    _parent: dict = field(default_factory=dict)
    _agent_at: dict = field(default_factory=dict)
    _model_at: dict = field(default_factory=dict)

    def nearest_role(self, span_id) -> str:
        sid, hops = span_id, 0
        while sid is not None and hops < 200:
            if sid in self._agent_at:
                return self._agent_at[sid]
            sid = self._parent.get(sid)
            hops += 1
        return UNATTRIBUTED

    def by_role(self) -> dict[str, dict[str, int]]:
        """role -> {prompt_tokens, completion_tokens, calls}."""
        out: dict[str, dict[str, int]] = {}
        for c in self.calls:
            r = out.setdefault(c.role, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
            r["prompt_tokens"] += c.prompt_tokens
            r["completion_tokens"] += c.completion_tokens
            r["calls"] += 1
        return out

    def tools_by_caller(self) -> dict[str, list[str]]:
        """caller role -> ordered list of tool names it invoked (with repeats)."""
        out: dict[str, list[str]] = {}
        for t in self.tools:
            out.setdefault(t.caller, []).append(t.tool)
        return out

    def by_role_model(self) -> dict[tuple[str, str | None], dict[str, int]]:
        """(role, model) -> {prompt_tokens, completion_tokens, calls}."""
        out: dict[tuple[str, str | None], dict[str, int]] = {}
        for c in self.calls:
            k = (c.role, c.model)
            r = out.setdefault(k, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
            r["prompt_tokens"] += c.prompt_tokens
            r["completion_tokens"] += c.completion_tokens
            r["calls"] += 1
        return out


class _SpanHandler(BaseSpanHandler):
    """Records span parentage and which spans are named-agent runs."""

    class _S:  # BaseSpanHandler expects new_span to return a span-like object
        pass

    def __init__(self, cap: UsageCapture):
        super().__init__()
        # store on a plain attribute; BaseSpanHandler is a pydantic model so avoid field clash
        object.__setattr__(self, "_cap", cap)

    def new_span(self, id_, bound_args, instance=None, parent_span_id=None, tags=None, **kwargs):
        cap: UsageCapture = object.__getattribute__(self, "_cap")
        cap._parent[id_] = parent_span_id
        if isinstance(instance, FunctionAgent) and getattr(instance, "name", None):
            cap._agent_at[id_] = instance.name
        # Capture the model/provider on the LLM span so a call can report which model ran.
        model = getattr(instance, "model", None)
        if model is not None:
            cap._model_at[id_] = (model, type(instance).__name__)
        # Tool spans: FunctionTool.acall / .call. The instance is the FunctionTool, whose
        # metadata.name is the tool (a sub-agent name at the coordinator, a real tool at a
        # sub-agent). Attribute it to the nearest enclosing named agent.
        tool_name = _tool_name(instance)
        if tool_name is not None:
            caller = cap.nearest_role(parent_span_id)
            cap.tools.append(ToolInvocation(caller=caller, tool=tool_name))
        s = _SpanHandler._S()
        s.id_ = id_
        return s

    def prepare_to_exit_span(self, id_, bound_args, instance=None, result=None, **kwargs):
        cap: UsageCapture = object.__getattribute__(self, "_cap")
        if isinstance(instance, FunctionAgent) and getattr(instance, "name", None) and result is not None:
            cap.agent_outputs[instance.name] = str(result)
        return None

    def prepare_to_drop_span(self, id_, bound_args, instance=None, err=None, **kwargs):
        return None


class _EventHandler(BaseEventHandler):
    """Turns each ``LLMChatEndEvent`` into an attributed :class:`LlmCall`."""

    def __init__(self, cap: UsageCapture):
        super().__init__()
        object.__setattr__(self, "_cap", cap)

    def handle(self, event, **kwargs):
        if type(event).__name__ != "LLMChatEndEvent":
            return event
        cap: UsageCapture = object.__getattribute__(self, "_cap")
        span_id = getattr(event, "span_id", None)
        resp = getattr(event, "response", None)
        ak = getattr(resp, "additional_kwargs", None) or {}
        prompt, completion = _extract_tokens(ak)
        model, provider = _resolve_model(cap, span_id)
        cap.calls.append(
            LlmCall(
                role=cap.nearest_role(span_id),
                model=model,
                provider=provider,
                prompt_tokens=prompt,
                completion_tokens=completion,
            )
        )
        return event


def _tool_name(instance) -> str | None:
    """Return the tool name if ``instance`` is a LlamaIndex FunctionTool, else None.

    Detected structurally (``instance.metadata.name``) to avoid importing the tool class and
    to tolerate subclasses; guarded so non-tool spans return None.
    """
    meta = getattr(instance, "metadata", None)
    if meta is None:
        return None
    name = getattr(meta, "name", None)
    # Only treat it as a tool span when the instance actually looks like a FunctionTool.
    if name and type(instance).__name__.endswith("Tool"):
        return name
    return None


def _get(obj, key):
    """Read ``key`` from a dict or an attribute off a pydantic/usage object."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_tokens(additional_kwargs: dict) -> tuple[int, int]:
    """Provider-agnostic (prompt, completion) token extraction from an LLM response's
    ``additional_kwargs`` (verified across providers in the spike):

    - OpenAILike (OpenRouter/groq/xai/compat): flat ``prompt_tokens`` / ``completion_tokens``.
    - OpenAIResponses (openai): nested ``usage`` object (``ResponseUsage``) with
      ``input_tokens`` / ``output_tokens``.
    - Anthropic / others: nested ``usage`` with ``input_tokens`` / ``output_tokens`` or the
      flat OpenAI-style names. All shapes are tried; missing counts fall back to 0.
    """
    ak = additional_kwargs or {}

    # 1) flat OpenAI chat-completions style
    p = ak.get("prompt_tokens")
    c = ak.get("completion_tokens")
    if p is not None or c is not None:
        return int(p or 0), int(c or 0)

    # 2) flat Responses style
    it = ak.get("input_tokens")
    ot = ak.get("output_tokens")
    if it is not None or ot is not None:
        return int(it or 0), int(ot or 0)

    # 3) nested usage object/dict (Responses `ResponseUsage`, Anthropic, etc.)
    usage = ak.get("usage")
    if usage is not None:
        p = _get(usage, "prompt_tokens")
        c = _get(usage, "completion_tokens")
        if p is not None or c is not None:
            return int(p or 0), int(c or 0)
        it = _get(usage, "input_tokens")
        ot = _get(usage, "output_tokens")
        if it is not None or ot is not None:
            return int(it or 0), int(ot or 0)

    return 0, 0


def _resolve_model(cap: UsageCapture, span_id):
    """Nearest ancestor span that recorded a model -> (model, provider-class)."""
    sid, hops = span_id, 0
    while sid is not None and hops < 200:
        if sid in cap._model_at:
            return cap._model_at[sid]
        sid = cap._parent.get(sid)
        hops += 1
    return (None, None)


_DEEPEVAL_INSTRUMENTED = False


def _ensure_deepeval_instrumented() -> None:
    """Enable deepeval's LlamaIndex instrumentation once (for the metrics path)."""
    global _DEEPEVAL_INSTRUMENTED
    if _DEEPEVAL_INSTRUMENTED:
        return
    try:
        from deepeval.integrations.llama_index import instrument_llama_index

        instrument_llama_index(get_dispatcher())
        _DEEPEVAL_INSTRUMENTED = True
    except Exception:
        # Metrics that read the trace may degrade, but token/cost capture is independent.
        pass


@contextlib.contextmanager
def capture_run(enable_deepeval: bool = True):
    """Context manager that captures per-agent token usage for the enclosed run(s).

    Usage::

        with capture_run() as cap:
            reply = await handle_message(...)
        usage = cap.by_role()

    Handlers are registered on the shared LlamaIndex dispatcher for the duration and removed
    on exit, so captures do not leak across evaluations.
    """
    cap = UsageCapture()
    if enable_deepeval:
        _ensure_deepeval_instrumented()
    dispatcher = get_dispatcher()
    span_h = _SpanHandler(cap)
    event_h = _EventHandler(cap)
    dispatcher.add_span_handler(span_h)
    dispatcher.add_event_handler(event_h)
    try:
        yield cap
    finally:
        # Best-effort removal; the dispatcher keeps handlers in plain lists.
        with contextlib.suppress(ValueError):
            dispatcher.span_handlers.remove(span_h)
        with contextlib.suppress(ValueError):
            dispatcher.event_handlers.remove(event_h)
