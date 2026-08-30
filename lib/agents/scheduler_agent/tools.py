"""scheduler_agent tools: create/list/delete/update proactive cron jobs & reminders,
thin over the job store (lib.domain). Registered into TOOLS_BY_AGENT["scheduler_agent"]
via @tool_ownership.

A scheduled job is a natural-language task for Jarvis plus WHEN to run it. When it
fires, lib/runtime/scheduler_runner feeds the prompt back through the same coordinator
with origin="system" and pushes the reply to the channel the job was created on. The
delivery channel/target are derived server-side from the current conversation
(ctx.conversation_id): the model never sees or handles the raw Messenger PSID."""

import logging
from typing import Optional

from llama_index.core.workflow import Context

from lib.core import tool_ownership, function_tool, app_context
from lib.domain import (
    store as scheduler_store,
    build_job,
    build_watch,
    public_view as scheduler_public_view,
    WATCH_SOURCES,
)

logger = logging.getLogger(__name__)


def _channel_from_ctx(ctx: Context) -> tuple[str, Optional[str], str]:
    """Resolve (channel, target, conversation_id) for the current turn.

    'messenger:{psid}' -> deliver via Messenger to that PSID; anything else (e.g.
    'repl') -> the 'log' channel (no live socket to push to)."""
    conv = getattr(app_context(ctx), "conversation_id", None) or "repl"
    if conv.startswith("messenger:"):
        return "messenger", conv.split(":", 1)[1], conv
    return "log", None, conv


@tool_ownership("scheduler_agent")
@function_tool
async def create_scheduled_job(
    ctx: Context,
    prompt: str,
    cron_expr: Optional[str] = None,
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    until: Optional[str] = None,
    max_runs: Optional[int] = None,
) -> dict:
    """
    Description:
        Schedule Jarvis to run a task on its own in the future and message the user
        with the result — a reminder or a recurring proactive brief. When the job
        fires, `prompt` is executed by the full assistant (all subagents) exactly as
        if the user had asked it, and the reply is delivered back on the same channel
        the user is on now. Use this for "przypomnij mi za...", "codziennie o...",
        "co godzinę...", etc.

    Parameters:
    ctx : Context
        Context in which the tool operates (also carries the delivery channel).

    prompt: str
        The task to run when the job fires, written as a complete, self-contained
        instruction in the user's language — e.g. "Przypomnij mi o spotkaniu z Anią."
        or "Podaj prognozę pogody na dziś w Warszawie, krótki przegląd najważniejszych
        wiadomości, oraz sprawdź, czy dziś grają zawodnicy z mojego składu FPL."

    cron_expr: Optional[str]
        A standard 5-field cron expression for a RECURRING job, in the assistant's
        timezone (Europe/Warsaw). Examples: "0 8 * * *" (every day 08:00),
        "0 8 * * 1-5" (weekdays 08:00), "30 18 * * 1" (Mondays 18:30).

    run_at: Optional[str]
        ISO-8601 date-time for a ONE-OFF job at an absolute moment, e.g.
        "2026-08-23T09:00". Use the environment date/time to resolve "jutro", a
        weekday, etc. into a concrete value.

    delay_minutes: Optional[int]
        Minutes from now for a ONE-OFF job — the simplest way to do "za X" without
        date maths. "za dwie godziny" -> 120, "za pół godziny" -> 30.

        Provide EXACTLY ONE of cron_expr / run_at / delay_minutes.

    until: Optional[str]
        Optional end boundary for a recurring job (ISO date or date-time), inclusive.
        For "przez miesiąc" set it a month ahead. Ignored for one-off jobs.

    max_runs: Optional[int]
        Optional cap on how many times a recurring job runs before it stops.

    Output:
        JSON with the created job (id, human-readable schedule, next run time) — never
        the raw delivery target. On invalid input, an {"Error": ...} object; relay it
        so the user can correct the request.
    """
    channel, target, conv = _channel_from_ctx(ctx)
    try:
        job = build_job(
            prompt=prompt, channel=channel, target=target, conversation_id=conv,
            cron_expr=cron_expr, run_at=run_at, delay_minutes=delay_minutes,
            until=until, max_runs=max_runs,
        )
    except ValueError as e:
        return {"Error": str(e)}
    scheduler_store.add(job)
    logging.info(f"Scheduled job {job['id']} created ({job['kind']}, next {job['next_run_at']})")
    return {"scheduled": scheduler_public_view(job)}


@tool_ownership("scheduler_agent")
@function_tool
async def create_watch(
    ctx: Context,
    message: str,
    source: str,
    check_code: str,
    poll_interval: int,
    params: Optional[dict] = None,
) -> dict:
    """
    Description:
        Create a CONDITIONAL watch: instead of firing at a time, Jarvis repeatedly polls a
        data source and notifies the user ONCE, the moment a condition becomes true. Use
        this for "daj znać, gdy...", "powiadom mnie, jak tylko...", "kiedy kurs spadnie
        poniżej...". The watch checks `source` every `poll_interval` seconds, evaluates
        `check_code` against the fetched data, and delivers `message` on the first
        false->true transition, then stops.

    Parameters:
    ctx : Context
        Context in which the tool operates (also carries the delivery channel).

    message: str
        The exact text to deliver to the user when the condition first becomes true
        (in the user's language), e.g. "Kurs USD/PLN spadł poniżej 3,90.".

    source: str
        Which data source to poll. Must be one of the known source ids below.
        Currently available:
          - finance.fx: currency exchange rate. params: {"foreign_currency": "USD",
            "base_currency": "PLN" (optional, default PLN)}. Data has the numeric "rate".
          - finance.quote: stock/index quote. params: {"symbol": "AAPL" or "PKN.WA"
            for GPW}. Data has the latest price.

    check_code: str
        The condition, as a small function `def check(data): return <bool>` over the
        source's data dict. Example: `def check(data): return data["rate"] < 3.9`. It is
        stored as-is here (generation/validation of this code is handled elsewhere).

    poll_interval: int
        How often to check, in seconds (positive). Pick a value proportional to how fast
        the data changes; avoid needlessly small intervals.

    params: Optional[dict]
        Parameters for the chosen source (see the descriptions above).

    Output:
        JSON with the created watch (id, human-readable schedule, next check time) — never
        the raw delivery target or the internal predicate. On invalid input, an
        {"Error": ...} object; relay it so the user can correct the request.
    """
    if source not in WATCH_SOURCES:
        return {
            "Error": f"unknown source '{source}'",
            "known_sources": sorted(WATCH_SOURCES),
        }
    channel, target, conv = _channel_from_ctx(ctx)
    try:
        job = build_watch(
            message=message, source=source, params=params or {}, check_code=check_code,
            poll_interval=poll_interval, channel=channel, target=target,
            conversation_id=conv,
        )
    except ValueError as e:
        return {"Error": str(e)}
    scheduler_store.add(job)
    logging.info(f"Watch {job['id']} created (source={source}, every {poll_interval}s)")
    return {"watch": scheduler_public_view(job)}


@tool_ownership("scheduler_agent")
@function_tool
async def list_scheduled_jobs(ctx: Context) -> dict:
    """
    Description:
        List the user's active scheduled jobs (reminders and recurring tasks) for the
        current conversation, with their schedules and next run times. Use it to
        answer "co mam zaplanowane", or before deleting/updating a job to find its id.

    Parameters:
    ctx : Context
        Context in which the tool operates.

    Output:
        JSON with the list of active jobs (id, prompt, schedule, next run) and count.
    """
    _, _, conv = _channel_from_ctx(ctx)
    jobs = [scheduler_public_view(j) for j in scheduler_store.for_conversation(conv)]
    return {"jobs": jobs, "count": len(jobs)}


@tool_ownership("scheduler_agent")
@function_tool
async def delete_scheduled_job(ctx: Context, job_id: str) -> dict:
    """
    Description:
        Cancel a scheduled job by its id (from list_scheduled_jobs). Use it for
        "odwołaj przypomnienie", "przestań mi wysyłać...", etc.

    Parameters:
    ctx : Context
        Context in which the tool operates.

    job_id: str
        The id of the job to cancel (e.g. 'job_1a2b3c4d').

    Output:
        JSON confirming deletion, or an error if the id was not found.
    """
    logging.info(f"Deleting scheduled job {job_id}")
    return {"deleted": job_id} if scheduler_store.delete(job_id) else {"Error": f"No scheduled job with id '{job_id}'"}


@tool_ownership("scheduler_agent")
@function_tool
async def update_scheduled_job(
    ctx: Context,
    job_id: str,
    prompt: Optional[str] = None,
    until: Optional[str] = None,
    max_runs: Optional[int] = None,
) -> dict:
    """
    Description:
        Adjust an existing scheduled job (from list_scheduled_jobs): change what it
        says (prompt) or its limits (until / max_runs). To change the TIMING itself,
        delete the job and create a new one instead.

    Parameters:
    ctx : Context
        Context in which the tool operates.

    job_id: str
        The id of the job to update (e.g. 'job_1a2b3c4d').

    prompt: Optional[str]
        New task text, if changing it.

    until: Optional[str]
        New end boundary (ISO date or date-time), if changing it.

    max_runs: Optional[int]
        New cap on remaining runs, if changing it.

    Output:
        JSON with the updated job, or an error if the id was not found.
    """
    logging.info(f"Updating scheduled job {job_id}")
    job = scheduler_store.update(job_id, prompt=prompt, until=until, max_runs=max_runs)
    return {"updated": scheduler_public_view(job)} if job else {"Error": f"No scheduled job with id '{job_id}'"}
