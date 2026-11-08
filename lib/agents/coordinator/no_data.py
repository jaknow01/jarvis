"""The coordinator's `no_data_needed` escape tool, for forced tool choice.

With ``COORDINATOR_FORCE_TOOL`` on, the coordinator's first LLM step must call a tool
(``FunctionAgent(initial_tool_choice="required")``) so it can't skip delegation and answer
from history. A message that needs no data at all ("dzięki!", "cześć") would then be pushed
into an arbitrary sub-agent, so this tool is the legitimate way out.

It only lets the coordinator reply without a sub-agent; the coordinator still writes that
reply itself, under its prompt rules (no facts or numbers, since nothing was gathered). It
also covers a scheduled plain reminder: the job's text is the whole message, so the
coordinator writes the reminder from it. Each use is logged with its declared kind so misuse
can be measured in evals.
"""

import logging
from typing import Literal

from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import Context

from lib.core import app_context

logger = logging.getLogger(__name__)

TOOL_NAME = "no_data_needed"

NoDataKind = Literal["greeting", "thanks", "small_talk", "acknowledgement", "farewell", "reminder"]


async def no_data_needed(ctx: Context, kind: NoDataKind) -> str:
    """Declare that the user's message is pure conversation that needs NO data and NO action.

    Use ONLY for greetings, thanks, small talk, a bare acknowledgement ("ok", "jasne"), a
    farewell, or a SCHEDULED plain reminder that only has to be delivered (kind="reminder").
    NEVER use it for a question (including follow-ups like "a funt?"), a request for any
    current information, or any action — those MUST go to the matching subagent. After calling
    it you write the reply yourself, with no facts or numbers (nothing was gathered); for a
    reminder, the reply is the reminder itself.

    Args:
        kind: which kind of purely conversational message this is.
    """
    app = app_context(ctx)
    app.no_data_kind = kind
    logger.info("Coordinator declared no data needed (kind=%s)", kind)
    return ("OK: no data gathered. Now write the reply yourself: no facts, numbers or claimed actions. "
            "For a scheduled reminder, the reply is the reminder text itself (e.g. 'Przypominam o ...').")


def no_data_tool() -> FunctionTool:
    return FunctionTool.from_defaults(async_fn=no_data_needed, name=TOOL_NAME)
