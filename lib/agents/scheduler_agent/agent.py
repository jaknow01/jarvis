import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="scheduler_agent")
def create_scheduler_agent() -> FunctionAgent:
    name = "scheduler_agent"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Schedules Jarvis to act on its own later: one-off reminders and recurring proactive briefs, plus listing/cancelling them.",
        system_prompt=(
            "You manage the user's scheduled jobs: one-off reminders and recurring proactive\
            tasks that Jarvis runs on its own and then messages the user about.\
            \
            Tool guide:\
            - create_scheduled_job: schedule a task. Provide EXACTLY ONE of:\
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
            - list_scheduled_jobs: show active jobs (with ids) for the current conversation.\
            - delete_scheduled_job: cancel a job by id (find it via list first).\
            - update_scheduled_job: change a job's prompt or its until/max_runs limits; to change\
              the timing itself, delete and re-create.\
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
