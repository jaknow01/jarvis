"""memory_operator tools: thin CRUD over the long-term memory store (lib.domain).
Registered into TOOLS_BY_AGENT["memory_operator"] via @tool_ownership."""

import logging
from typing import Literal, Optional

from llama_index.core.workflow import Context

from lib.core import tool_ownership, function_tool, app_context
from lib.domain import memory

logger = logging.getLogger(__name__)


@tool_ownership("memory_operator")
@function_tool
async def get_memory(ctx: Context, category: Optional[str] = None) -> dict:
    """
    Description:
        Retrieve the user's stored long-term memory (durable preferences, facts,
        habits, interests, routines). Use this to personalize answers and to avoid
        re-asking things the user already told you.

    Parameters:
    ctx : Context
        Context in which the tool operates

    category: Optional[str]
        If given, return only entries in this category (e.g. 'preferences',
        'facts', 'habits', 'interests', 'routines'). If omitted, return everything.

    Output:
        JSON object with the matching memory entries and their count.
    """
    logging.info(f"Reading long-term memory (category={category or 'all'})")
    entries = memory.by_category(category) if category else memory.all()
    return {"entries": entries, "count": len(entries)}

@tool_ownership("memory_operator")
@function_tool
async def save_memory(ctx: Context,
                      text: str,
                      category: str = "preferences",
                      source: Literal["user", "inferred"] = "user",
                      confidence: Literal["high", "medium", "low"] = "high") -> dict:
    """
    Description:
        Store a new durable memory about the user. Use it when the user states a
        lasting preference or fact, or when you reliably infer one. Write a concise,
        self-contained statement in natural language. Do NOT store transient or
        one-off details.

    Parameters:
    ctx : Context
        Context in which the tool operates

    text: str
        The memory to store, as a single self-contained natural-language sentence.

    category: str = "preferences"
        Grouping label. Suggested: 'preferences', 'facts', 'habits', 'interests',
        'routines'.

    source: Literal["user", "inferred"] = "user"
        'user' when the user stated it explicitly; 'inferred' when you concluded it.

    confidence: Literal["high", "medium", "low"] = "high"
        How sure you are of this memory.

    Output:
        JSON object with the saved entry (including its generated id).
    """
    logging.info(f"Saving long-term memory (category={category}, source={source})")
    try:
        entry = memory.add(text, category=category, source=source, confidence=confidence)
    except ValueError as e:
        return {"Error": str(e)}
    return {"saved": entry}

@tool_ownership("memory_operator")
@function_tool
async def update_memory(ctx: Context,
                        entry_id: str,
                        text: Optional[str] = None,
                        category: Optional[str] = None,
                        confidence: Optional[Literal["high", "medium", "low"]] = None) -> dict:
    """
    Description:
        Correct or refine an existing memory entry, identified by its id (from
        get_memory). Only the provided fields are changed.

    Parameters:
    ctx : Context
        Context in which the tool operates

    entry_id: str
        The id of the entry to update (e.g. 'mem_1a2b3c4d').

    text: Optional[str]
        New text, if changing it.

    category: Optional[str]
        New category, if changing it.

    confidence: Optional[Literal["high", "medium", "low"]]
        New confidence, if changing it.

    Output:
        JSON object with the updated entry, or an error if the id was not found.
    """
    logging.info(f"Updating long-term memory {entry_id}")
    entry = memory.update(entry_id, text=text, category=category, confidence=confidence)
    return {"updated": entry} if entry else {"Error": f"No memory entry with id '{entry_id}'"}

@tool_ownership("memory_operator")
@function_tool
async def delete_memory(ctx: Context, entry_id: str) -> dict:
    """
    Description:
        Permanently remove a memory entry by its id (from get_memory). Use when a
        memory is wrong or no longer relevant.

    Parameters:
    ctx : Context
        Context in which the tool operates

    entry_id: str
        The id of the entry to delete (e.g. 'mem_1a2b3c4d').

    Output:
        JSON object confirming deletion, or an error if the id was not found.
    """
    logging.info(f"Deleting long-term memory {entry_id}")
    return {"deleted": entry_id} if memory.delete(entry_id) else {"Error": f"No memory entry with id '{entry_id}'"}
