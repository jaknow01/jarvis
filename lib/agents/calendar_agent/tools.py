"""calendar_agent tools: read/write access to the owner's Apple iCloud calendar over
CalDAV. Registered into TOOLS_BY_AGENT["calendar_agent"] via @tool_ownership.

Credentials never reach the model — they live in the environment and are used only inside
``caldav_link`` (see that module's docstring). Tools take/return event summaries, ISO-8601
times and opaque event ids (UIDs). All CalDAV calls are synchronous, so each tool offloads
them to a worker thread with ``asyncio.to_thread`` to keep the event loop free.

Dates: every agent's system prompt is prefixed with the environment preamble (current
date/time/timezone, see lib/core/env.py), so the model resolves 'jutro'/'w piątek' to a
concrete date itself and passes ISO-8601 strings here. Naive datetimes are interpreted in
the owner's timezone server-side.
"""

import asyncio
import logging

from llama_index.core.workflow import Context

from lib.core import tool_ownership, function_tool
from lib.agents.calendar_agent import caldav_link

logger = logging.getLogger(__name__)


@tool_ownership("calendar_agent")
@function_tool
async def list_calendar_events(ctx: Context, start: str, end: str, calendar_name: str = "") -> dict:
    """
    Description:
        Lists the owner's calendar events in a time window (from the Apple iCloud
        calendar). Use it to answer "what's on my calendar", "am I free on...", "what do
        I have tomorrow / this week". Recurring events are expanded into their individual
        occurrences within the window.

    Parameters:
    start: str
        Start of the window as an ISO-8601 datetime, e.g. "2026-09-15T00:00:00".
        Resolve relative days ("today", "tomorrow", "jutro", weekday names) against the
        current date given in the environment context before calling.
    end: str
        End of the window as an ISO-8601 datetime, e.g. "2026-09-16T00:00:00".
    calendar_name: str = ""
        Optional calendar display name to read from. Empty means the configured default
        (ICLOUD_CALENDAR_NAME) or the account's first calendar.

    Output:
        JSON object: {"events": [ {id, summary, start, end, all_day, location,
        description, status}, ... ]}. Each event's "id" (UID) is what update/delete tools
        take. An empty list means nothing is scheduled in that window.
    """
    try:
        events = await asyncio.to_thread(
            caldav_link.list_events, start, end, calendar_name or None
        )
    except caldav_link.CalendarConfigError as e:
        return {"Error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface the failure to the composer
        logger.exception("list_calendar_events failed")
        return {"Error": f"Could not read the calendar: {e}"}
    logger.info("Listed %d calendar event(s) for %s..%s", len(events), start, end)
    return {"events": events, "count": len(events)}


@tool_ownership("calendar_agent")
@function_tool
async def create_calendar_event(
    ctx: Context,
    summary: str,
    start: str,
    end: str = "",
    all_day: bool = False,
    location: str = "",
    description: str = "",
    calendar_name: str = "",
) -> dict:
    """
    Description:
        Creates a new event in the owner's Apple iCloud calendar. Use it for "add /
        schedule / put in my calendar ...", "dodaj do kalendarza ...".

    Parameters:
    summary: str
        The event title (what the user calls it), e.g. "Dentysta".
    start: str
        Start as an ISO-8601 datetime ("2026-09-20T15:00:00"). For an all_day event a
        date is enough ("2026-09-20"). Resolve relative days against the environment
        context's current date first.
    end: str = ""
        End as an ISO-8601 datetime. If omitted: a timed event defaults to 1 hour, an
        all_day event to a single day.
    all_day: bool = False
        True for a full-day event with no specific time.
    location: str = ""
        Optional place.
    description: str = ""
        Optional notes/body.
    calendar_name: str = ""
        Optional target calendar display name; empty means the configured default.

    Output:
        JSON object with the created event (including its "id"), or {"Error": ...}.
    """
    try:
        event = await asyncio.to_thread(
            lambda: caldav_link.create_event(
                summary=summary,
                start_iso=start,
                end_iso=end or None,
                all_day=all_day,
                location=location or None,
                description=description or None,
                calendar_name=calendar_name or None,
            )
        )
    except caldav_link.CalendarConfigError as e:
        return {"Error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.exception("create_calendar_event failed")
        return {"Error": f"Could not create the event: {e}"}
    logger.info("Created calendar event %r at %s", summary, start)
    return {"created": event}


@tool_ownership("calendar_agent")
@function_tool
async def update_calendar_event(
    ctx: Context,
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    location: str = "",
    description: str = "",
    calendar_name: str = "",
) -> dict:
    """
    Description:
        Edits an existing calendar event. Change only the fields you pass; leave the rest
        empty to keep them. First identify the event with list_calendar_events to get its
        "id".

    Parameters:
    event_id: str
        The event UID (from list_calendar_events).
    summary: str = ""
        New title, or empty to keep.
    start: str = ""
        New start (ISO-8601), or empty to keep.
    end: str = ""
        New end (ISO-8601), or empty to keep.
    location: str = ""
        New location, or empty to keep.
    description: str = ""
        New notes, or empty to keep.
    calendar_name: str = ""
        Optional calendar display name; empty means the configured default.

    Output:
        JSON object with the updated event, {"Error": "Event not found"} if the id does
        not exist, or {"Error": ...} on failure.
    """
    try:
        event = await asyncio.to_thread(
            lambda: caldav_link.update_event(
                event_id,
                summary=summary or None,
                start_iso=start or None,
                end_iso=end or None,
                location=location or None,
                description=description or None,
                calendar_name=calendar_name or None,
            )
        )
    except caldav_link.CalendarConfigError as e:
        return {"Error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.exception("update_calendar_event failed")
        return {"Error": f"Could not update the event: {e}"}
    if event is None:
        return {"Error": "Event not found", "event_id": event_id}
    logger.info("Updated calendar event %s", event_id)
    return {"updated": event}


@tool_ownership("calendar_agent")
@function_tool
async def delete_calendar_event(
    ctx: Context, event_id: str, confirm: bool = False, calendar_name: str = ""
) -> dict:
    """
    Description:
        Deletes a calendar event. Deletion is IRREVERSIBLE and therefore GATED: you MUST
        NOT delete without the user's explicit confirmation of the specific event.

        Two-step flow:
        1. Call FIRST with confirm=false (the default). The event is NOT deleted; the tool
           returns the event's details under "pending_deletion". Show that event to the
           user and ask them to confirm.
        2. Only AFTER the user explicitly confirms (in their reply) call again with
           confirm=true to actually delete it.
        Never set confirm=true on the strength of the original request alone — wait for a
        distinct confirmation turn.

    Parameters:
    event_id: str
        The event UID (from list_calendar_events).
    confirm: bool = False
        False = preview only (safe). True = perform the deletion; use only after the user
        has explicitly confirmed this exact event.
    calendar_name: str = ""
        Optional calendar display name; empty means the configured default.

    Output:
        - confirm=false: {"pending_deletion": {event...}, "requires_confirmation": true} —
          ask the user to confirm before deleting.
        - confirm=true: {"deleted": true, "event_id": ...} on success.
        - {"Error": "Event not found"} if the id does not exist, or {"Error": ...}.
    """
    try:
        if not confirm:
            event = await asyncio.to_thread(
                caldav_link.get_event, event_id, calendar_name or None
            )
            if event is None:
                return {"Error": "Event not found", "event_id": event_id}
            return {"pending_deletion": event, "requires_confirmation": True}

        ok = await asyncio.to_thread(
            caldav_link.delete_event, event_id, calendar_name or None
        )
    except caldav_link.CalendarConfigError as e:
        return {"Error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.exception("delete_calendar_event failed")
        return {"Error": f"Could not delete the event: {e}"}
    if not ok:
        return {"Error": "Event not found", "event_id": event_id}
    logger.info("Deleted calendar event %s", event_id)
    return {"deleted": True, "event_id": event_id}
