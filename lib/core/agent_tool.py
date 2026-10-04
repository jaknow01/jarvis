"""`agent_as_tool()` — expose a LlamaIndex agent as a `FunctionTool`.

Replaces the OpenAI Agents SDK's `Agent.as_tool()`: the coordinator keeps its
"sub-agents are my tools" model (Pattern 2). Each sub-agent's `run()` is wrapped
in an async closure taking a single `prompt: str` (mirroring `as_tool()`'s single
string input) plus the workflow `Context` (auto-injected by annotation, hidden
from the model's tool schema).

Context sharing (design D3): the shared per-run `Ctx` rides on a contextvar bound
once per turn in `engine.handle_message` (see `lib.core.cache`). It propagates to
this closure — which runs inside a coordinator step-worker task — and onward to the
fresh sub-agent run, so every tool in the turn observes the same `Ctx`. Each
sub-agent still runs as its own workflow on its own fresh `Context`.
"""

import asyncio
import logging

from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import Context

from lib.core.cache import app_context
from lib.core.settings import env_int

logger = logging.getLogger(__name__)


def subagent_timeout_seconds() -> int:
    """Wall-clock cap on one sub-agent run, ``SUBAGENT_TIMEOUT_SECONDS`` (default 120).

    A hard deadline is needed on top of the LLM clients' own timeouts: those are per-read,
    so a streamed completion that keeps trickling (or a sub-agent looping over tool calls)
    never trips them. ``0`` disables the cap."""
    return env_int("SUBAGENT_TIMEOUT_SECONDS", 120, minimum=0)


# Tool-hygiene rules appended to every sub-agent's system prompt by `agent_as_tool`, so they
# reach all sub-agents from one place. Weaker (local) models otherwise answer from built-in
# knowledge, or re-call a tool that returned something unrelated until max_iterations.
SUBAGENT_TOOL_RULES = (
    "\n\nTool usage rules (apply to every request):\n"
    "- Always obtain data and perform actions through your tools. Never answer from your "
    "built-in knowledge or from earlier conversation, and never claim an action was done "
    "unless a tool call in THIS run confirmed it.\n"
    "- Sanity-check every tool result against the request (right currency pair/ticker/"
    "device/place/date, plausible values). If a result is clearly unrelated, malformed, "
    "empty or stale, you may retry ONCE with corrected arguments. If it is still wrong, "
    "STOP calling tools and report plainly what you asked for, what came back and that "
    "this part is unavailable — do not keep retrying and do not substitute invented values.\n"
    "- Never call the same tool with the same arguments more than twice in one run.\n"
    "- Return only facts the tools returned, keeping numbers, units and dates exact."
)


async def _cancel_quietly(handler) -> None:
    try:
        await handler.cancel_run()
    except Exception:
        logger.debug("Sub-agent cancel failed (ignored)", exc_info=True)


def agent_as_tool(agent, tool_name: str, tool_description: str) -> FunctionTool:
    """Wrap `agent` as a `FunctionTool` named `tool_name` with `tool_description`.

    The tool takes one argument, `prompt`, forwarded to the sub-agent as the user
    message. The sub-agent's final text is returned to the caller (the coordinator).

    The run is bounded by :func:`subagent_timeout_seconds`. A timeout or an unexpected
    failure is returned to the coordinator as a plain-text error with guidance (not
    raised), so one slow/broken sub-agent degrades the turn instead of stalling it.

    The agent's string ``system_prompt`` gets :data:`SUBAGENT_TOOL_RULES` appended (once).
    """
    prompt_text = getattr(agent, "system_prompt", None)
    if isinstance(prompt_text, str) and SUBAGENT_TOOL_RULES not in prompt_text:
        agent.system_prompt = prompt_text + SUBAGENT_TOOL_RULES

    async def _run(ctx: Context, prompt: str) -> str:
        try:
            app_context().subagent_calls.append(tool_name)
        except LookupError:
            pass  # no turn context bound (direct/test use)
        timeout = subagent_timeout_seconds()
        handler = agent.run(user_msg=prompt, ctx=Context(agent))
        try:
            result = await asyncio.wait_for(handler, timeout=timeout or None)
        except asyncio.TimeoutError:
            # wait_for only cancels its own awaiter; stop the sub-agent's workflow too so
            # its in-flight LLM call / tool loop doesn't keep running detached.
            await _cancel_quietly(handler)
            logger.warning("Sub-agent %s timed out after %ss", tool_name, timeout)
            return (
                f"ERROR: sub-agent '{tool_name}' did not finish within {timeout} s and was "
                "stopped. Do NOT call it again for the same request in this turn — continue "
                "with the data you already have and tell the user this part is unavailable."
            )
        except asyncio.CancelledError:
            asyncio.ensure_future(_cancel_quietly(handler))
            raise
        except Exception as e:
            logger.error("Sub-agent %s failed", tool_name, exc_info=True)
            return (
                f"ERROR: sub-agent '{tool_name}' failed ({type(e).__name__}: {e}). Retry at "
                "most once with a simpler request; otherwise tell the user this part is "
                "unavailable."
            )
        return str(result)

    _run.__name__ = tool_name
    _run.__doc__ = tool_description
    return FunctionTool.from_defaults(
        async_fn=_run, name=tool_name, description=tool_description
    )
