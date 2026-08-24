"""`function_tool` — turn a plain async function into a LlamaIndex `FunctionTool`.

The LlamaIndex analogue of the OpenAI Agents SDK's `@function_tool`. Used together
with `@tool_ownership` (outermost) in each agent's `tools.py`:

    @tool_ownership("weather_agent")
    @function_tool
    async def current_weather(ctx: Context, city: str = "Warsaw") -> dict:
        \"\"\"<docstring is the tool spec the model sees>\"\"\"
        ...

It also accepts the call form ``@function_tool(...)`` for API compatibility with the
old decorator (e.g. ``@function_tool(strict_mode=False)``); such keyword arguments
were OpenAI-SDK schema-strictness knobs and are not needed under LlamaIndex — the
tool schema (including nested pydantic-model parameters) is derived from the type
hints — so they are accepted and ignored.

The tool's name comes from the function name and its description/parameter docs from
the docstring (keep the existing detailed style — the docstring IS the tool spec).
A leading ``ctx: Context`` parameter is auto-injected at call time and hidden from
the model's tool schema.
"""

from llama_index.core.tools import FunctionTool


def function_tool(fn=None, **_ignored):
    """Wrap an async function as a `FunctionTool`. Usable bare or called with kwargs."""
    if fn is None:
        # Called with arguments, e.g. @function_tool(strict_mode=False)
        return lambda f: FunctionTool.from_defaults(fn=f)
    return FunctionTool.from_defaults(fn=fn)
