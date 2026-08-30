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
from lib.agents.scheduler_agent.authoring import author_watch
from lib.agents.scheduler_agent.watch_sandbox_client import resolve_evaluator

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
async def prepare_watch(
    ctx: Context,
    message: str,
    source: str,
    check_code: str,
    poll_interval: int,
    intent: str,
    params: Optional[dict] = None,
) -> dict:
    """
    Description:
        STEP 1 of setting up a CONDITIONAL watch ("daj znać, gdy...", "powiadom mnie,
        kiedy..."). This prepares and CHECKS the watch but does NOT arm it yet. It
        validates your `check_code`, auto-repairs it if needed, and runs it once against
        freshly fetched data so you can show the user what it currently returns. After the
        user confirms, call `create_watch` with the SAME `source`/`params`/`poll_interval`/
        `message` and the returned `check_code` to actually arm it.

    Parameters:
    ctx : Context
        Context in which the tool operates.

    message: str
        The exact text to deliver when the condition first becomes true (user's language).

    source: str
        Which data source to poll. One of the known source ids:
          - finance.fx: exchange rate. params: {"foreign_currency": "USD",
            "base_currency": "PLN" (optional)}. Data has the numeric "rate".
          - finance.quote: stock/index quote. params: {"symbol": "AAPL" or "PKN.WA"}.
            Data has the latest price.

    check_code: str
        The condition as `def check(data): return <bool>` in Starlark (a Python dialect:
        no import / try-except / f-strings / while). Use `get(data, "a.b", default)` for
        safe access. Example: `def check(data):\n    return data["rate"] < 3.9`.

    poll_interval: int
        How often to check, in seconds (positive).

    intent: str
        A short natural-language description of what the condition should detect (e.g.
        "USD/PLN below 3.90"). Used to auto-repair the code if it does not compile.

    params: Optional[dict]
        Parameters for the chosen source.

    Output:
        On success: {"prepared": {..., "check_code": <validated>, "current_result": <bool>}}
        — show `current_result` to the user and ask them to confirm before arming. If the
        code could not be validated or the data is unavailable, an {"Error": ...} object;
        relay it (ask the user to clarify the condition). NEVER treat prepare as armed.
    """
    if source not in WATCH_SOURCES:
        return {"Error": f"unknown source '{source}'", "known_sources": sorted(WATCH_SOURCES)}
    try:
        if int(poll_interval) <= 0:
            return {"Error": "poll_interval must be a positive number of seconds"}
    except (TypeError, ValueError):
        return {"Error": "poll_interval must be an integer number of seconds"}

    res = await author_watch(check_code, intent, source, params or {}, resolve_evaluator())
    if not res.ok:
        return {
            "Error": "could not validate the condition code",
            "detail": res.error,
            "repairs_tried": res.attempts,
            "hint": "ask the user to clarify the condition, then try again",
        }
    if res.smoke_state != "ok":
        return {
            "Error": "condition validated but the source data is currently unavailable",
            "validated_check_code": res.code,
            "hint": "try again later; do not arm the watch",
        }
    return {
        "prepared": {
            "message": message,
            "source": source,
            "params": params or {},
            "poll_interval": poll_interval,
            "check_code": res.code,          # validated (possibly repaired) code
            "current_result": res.smoke_result,
            "repairs_used": res.attempts,
        },
        "note": (
            "NOT armed. Tell the user what the condition currently returns (current_result) "
            "and ask them to confirm. Only after they confirm, call create_watch with this "
            "exact check_code."
        ),
    }


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
        STEP 2: ARM a conditional watch after the user has confirmed it. Call this only
        after `prepare_watch` succeeded and the user agreed, passing the SAME parameters and
        the validated `check_code` that prepare returned. The watch then polls `source`
        every `poll_interval` seconds, evaluates `check_code`, and delivers `message` once on
        the first false->true transition, then stops. The code is re-validated here as a
        safety net; do not call this with code that has not been through `prepare_watch`.

    Parameters:
    ctx : Context
        Context in which the tool operates (also carries the delivery channel).

    message: str
        The exact text to deliver when the condition first becomes true (user's language).

    source: str
        The same source id used in `prepare_watch` (e.g. finance.fx, finance.quote).

    check_code: str
        The validated `def check(data): return <bool>` returned by `prepare_watch`.

    poll_interval: int
        How often to check, in seconds (positive).

    params: Optional[dict]
        Parameters for the chosen source (same as in `prepare_watch`).

    Output:
        JSON with the armed watch (id, human-readable schedule, next check time) — never the
        raw delivery target or the predicate. On invalid input or code that fails
        re-validation, an {"Error": ...} object; relay it.
    """
    if source not in WATCH_SOURCES:
        return {
            "Error": f"unknown source '{source}'",
            "known_sources": sorted(WATCH_SOURCES),
        }
    # Safety net: never arm a watch whose predicate does not validate.
    ok, verr = await resolve_evaluator().validate(check_code)
    if not ok:
        return {"Error": f"check_code did not validate: {verr}", "hint": "run prepare_watch first"}
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
    logging.info(f"Watch {job['id']} armed (source={source}, every {poll_interval}s)")
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
