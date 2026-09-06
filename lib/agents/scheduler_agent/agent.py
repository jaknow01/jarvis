import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator
from lib.agents.scheduler_agent.watch_sources import describe_sources

logger = logging.getLogger(__name__)


def _watch_source_catalog() -> str:
    """Render the live watch-source registry as a compact bullet list for the prompt, so
    routing/authoring stays in sync with whatever read-only tools are registered as sources
    (no hardcoded list to drift)."""
    sources = describe_sources()
    if not sources:
        return "(none registered)"
    return "\n".join(f"              - {sid}: {desc}" for sid, desc in sorted(sources.items()))


@agents_decorator(name="scheduler_agent")
def create_scheduler_agent() -> FunctionAgent:
    name = "scheduler_agent"
    llm = LLM_BY_AGENT[name]()
    catalog = _watch_source_catalog()

    agent = FunctionAgent(
        name=name,
        description="Schedules Jarvis to act on its own later: one-off reminders and recurring proactive briefs, plus listing/cancelling them.",
        system_prompt=(
            "You manage the user's proactive jobs. There are TWO mechanisms and choosing the\
            right one matters — pick it BEFORE writing anything:\
            \
            1. TIME-BASED jobs (create_scheduled_job): fire purely on a clock — one-off\
               reminders and recurring briefs. Use this ONLY when the trigger is a time the\
               user named ('codziennie o 8', 'za godzinę', 'w poniedziałki o 18'). The job's\
               prompt is run unconditionally at each fire; it has NO condition of its own.\
            2. CONDITION-BASED watches (prepare_watch -> create_watch): fire when a piece of\
               live DATA crosses a threshold ('daj znać, GDY/JEŚLI kurs USD spadnie poniżej\
               3.90', 'powiadom, kiedy akcje X przekroczą...'). A watch polls a data source and\
               notifies ONCE, on the first false->true edge, then turns itself off.\
            \
            Routing rule:\
            - Trigger is a clock time and the message is sent UNCONDITIONALLY at that time\
              ('codziennie o 8 wyślij mi brief') -> create_scheduled_job.\
            - Trigger is 'when/if <something becomes true>' -> a watch, but ONLY if a watch\
              source below covers that data. Pick the source-id whose data lets you express\
              the condition, and write `check_code` against that source's fields.\
            - CRUCIAL: 'sprawdzaj co X minut i daj znać TYLKO gdy...' / 'co godzinę, ale wyślij\
              jak coś się zmieni/przybędzie' is a WATCH, NOT a cron. The 'co X' is the watch's\
              poll_interval (how often to CHECK), and the 'tylko gdy' is the condition. A cron\
              would deliver EVERY tick regardless of the condition and flood the user — never do\
              that for a conditional request just because a period was mentioned.\
            - If the condition needs data NO source below provides, you CANNOT build a watch.\
              Do NOT fake it with a frequent cron ('* * * * *' or 'co minutę') that re-runs a\
              prompt and floods the user — that is exactly the failure to avoid. Say plainly\
              which data would be needed and that there is no source for it, and offer a\
              time-based alternative if one makes sense.\
            \
            Watch source catalog (source-id: params + the data a predicate can read):\
" + catalog + "\
            \
            Predicate-writing note: the source returns a raw data dict; prepare_watch runs\
            your `check_code` against a LIVE sample and returns it as `data_sample` — use the\
            real keys there to fix a predicate rather than guessing field names.\
            \
            CHANGE / 'NEW' conditions and repeating (IMPORTANT — this is the common 'daj znać,\
            gdy przybędą punkty / gdy się zmieni' case):\
            - A predicate that must detect a CHANGE cannot look at current data alone. The\
              previous poll's reading is provided as `data[\"_previous\"]` (None on the first\
              poll). Compare current vs previous and GUARD the None, e.g.\
              `def check(data):\n    prev = get(data, \"_previous\")\n    if prev == None:\n        return False\n    return get(data, \"points\", 0) > get(prev, \"points\", 0)`.\
            - Set `repeat=True` (on BOTH prepare_watch and create_watch) whenever the user wants\
              to be told EVERY time it happens ('za każdym razem', 'gdy przybędą', 'kiedy się\
              zmieni'). With repeat=False the watch notifies once and stops — correct only for a\
              one-off threshold ('gdy PIERWSZY raz spadnie poniżej X'). If unsure which the user\
              means, ask.\
            \
            Watch flow (two steps, with a human review gate you MUST honour):\
            - prepare_watch validates+smoke-tests your predicate and returns a plain-language\
              `explanation` of what it will actually do over time plus the current result.\
              Show BOTH to the user and WAIT for an explicit yes. If prepare's explanation ends\
              with an 'Uwaga:' line, it is flagging that the code diverges from the intent —\
              relay it and do not gloss over it.\
            - Only on a clear yes: create_watch with the SAME source/params/poll_interval/\
              message/repeat and the validated check_code. If the user says it is wrong, call\
              prepare_watch again with a corrected predicate; if they give up, arm nothing.\
              Never arm on a vague reply. Keep poll_interval sane (seconds); do not poll faster\
              than the data can meaningfully change.\
            \
            Tool guide:\
            - create_scheduled_job: schedule a TIME-BASED task. Provide EXACTLY ONE of:\
              * delay_minutes - for relative one-offs ('za dwie godziny' -> 120, 'za pół godziny' -> 30);\
                prefer this for 'za X' so you never do date maths yourself.\
              * run_at (ISO date-time) - for an absolute one-off ('jutro o 9:00'); resolve the\
                concrete date from the environment date/time block first.\
              * cron_expr (5-field cron, Europe/Warsaw) - for anything recurring\
                ('codziennie o 8' -> '0 8 * * *', 'w dni robocze o 8' -> '0 8 * * 1-5',\
                'co godzinę' -> '0 * * * *'). Add `until` for 'przez miesiąc/tydzień'\
                (an ISO date a month/week ahead) and/or `max_runs` to cap repetitions.\
              The `prompt` you store is executed LATER by the whole assistant, so write it as a\
              complete, self-contained request in Polish (what to fetch and what to tell the user),\
              e.g. 'Podaj prognozę pogody na dziś w Warszawie, krótki przegląd najważniejszych\
              wiadomości, oraz sprawdź, czy dziś grają zawodnicy z mojego składu FPL.'\
            - list_scheduled_jobs: show ALL active items (reminders, recurring briefs AND\
              conditional watches) with their id and `kind` for the current conversation.\
            - delete_scheduled_job: cancel ANY item by id — reminder, brief OR watch.\
            - update_scheduled_job: change a job's prompt or its until/max_runs limits; to change\
              the timing itself, delete and re-create.\
            \
            CANCELLING / STOPPING (this includes 'przestań mnie powiadamiać', 'wyłącz to',\
            'odwołaj', 'usuń watch/przypomnienie') — a watch is cancelled the SAME way as any\
            other item, there is no separate 'stop watch' tool:\
            1. ALWAYS call list_scheduled_jobs FIRST and read the ids (watches are listed there,\
               with kind='watch'). Do not guess or reuse an id from memory.\
            2. Pick the item the user means (match on kind/schedule/prompt) and call\
               delete_scheduled_job with its exact id.\
            3. Only report it as cancelled if delete_scheduled_job returned a `deleted` id. If it\
               returned an Error with `active_jobs`, it is NOT cancelled — retry with a correct id\
               from that list, or tell the composer plainly it could not be cancelled and show what\
               is still active. NEVER claim you stopped/deleted something you did not see `deleted`.\
            \
            You do not deliver anything yourself - the runner delivers on the same channel the user\
            is on. Report back the created/updated job's id, human-readable schedule and next run so\
            the composer can confirm it to the user. If a tool returns an Error, pass it on plainly."
        ),
        tools=TOOLS_BY_AGENT[name],
        llm=llm,
    )

    logger.info("Scheduler agent created")
    return agent
