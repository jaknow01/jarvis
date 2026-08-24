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

from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import Context


def agent_as_tool(agent, tool_name: str, tool_description: str) -> FunctionTool:
    """Wrap `agent` as a `FunctionTool` named `tool_name` with `tool_description`.

    The tool takes one argument, `prompt`, forwarded to the sub-agent as the user
    message. The sub-agent's final text is returned to the caller (the coordinator).
    """

    async def _run(ctx: Context, prompt: str) -> str:
        result = await agent.run(user_msg=prompt, ctx=Context(agent))
        return str(result)

    _run.__name__ = tool_name
    _run.__doc__ = tool_description
    return FunctionTool.from_defaults(
        async_fn=_run, name=tool_name, description=tool_description
    )
