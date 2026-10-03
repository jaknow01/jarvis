"""The coordinator's `no_data_needed` escape tool, for forced tool choice.

With ``COORDINATOR_FORCE_TOOL`` on, the coordinator's first LLM step must call a tool
(``FunctionAgent(initial_tool_choice="required")``) so it can't skip delegation and answer
from history. A message that needs no data at all ("dzięki!", "cześć") would then be pushed
into an arbitrary sub-agent, so this tool is the legitimate way out.

It cannot become a hallucination route: it gathers nothing, and when it is the only thing
the coordinator called in a turn, the engine discards the coordinator's free-text summary
and gives the composer a fixed "no data gathered" brief instead (see
``lib.runtime.engine``). Misuse therefore degrades to "I don't have that right now",
never to invented figures. Each use is logged with its declared kind so misuse can be
measured in evals.
"""

import logging
from typing import Literal

from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import Context

from lib.core import app_context

logger = logging.getLogger(__name__)

TOOL_NAME = "no_data_needed"

NoDataKind = Literal["greeting", "thanks", "small_talk", "acknowledgement", "farewell"]


async def no_data_needed(ctx: Context, kind: NoDataKind) -> str:
    """Declare that the user's message is pure conversation that needs NO data and NO action.

    Use ONLY for greetings, thanks, small talk, a bare acknowledgement ("ok", "jasne") or a
    farewell. NEVER use it for a question (including follow-ups like "a funt?"), a request for
    any current information, or any action — those MUST go to the matching subagent. Calling
    this tool means the reply will contain no facts or numbers at all.

    Args:
        kind: which kind of purely conversational message this is.
    """
    app = app_context(ctx)
    app.no_data_kind = kind
    logger.info("Coordinator declared no data needed (kind=%s)", kind)
    return "OK: no data gathered for this message. Do not add any facts, numbers or actions."


def no_data_tool() -> FunctionTool:
    return FunctionTool.from_defaults(async_fn=no_data_needed, name=TOOL_NAME)
