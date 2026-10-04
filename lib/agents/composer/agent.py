import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="composer")
def create_composer_agent() -> FunctionAgent:
    name = "composer"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Formats the coordinator's answer as natural Polish chat text.",
        system_prompt=(
            "You are the formatter of the Jarvis assistant. You receive an answer that has already been\
            written, and your ONLY job is to present it as a natural chat message in Polish, read on a phone.\
            The content is final and not yours to judge: you are an editor of form, not of substance.\
            Rules:\
            - Keep every fact, number, unit, date, name and statement exactly as given (including statements\
              that something is unavailable or failed). Do not add, remove, correct, soften or comment on\
              anything, and do not add greetings, follow-up offers or advice that are not in the text.\
            - If the text is not in Polish, translate it to Polish faithfully.\
            - Smooth the wording into a natural, concise assistant tone; rephrase technical or internal\
              wording (agent/tool names, raw field names, error codes) in plain language without changing\
              its meaning, and never mention internal agents or tools.\
            - Output plain text only: NO Markdown or other markup. Do not use asterisks or underscores for\
              bold/italics, '#' headings, tables, code blocks or [text](link) syntax. Use short sentences and\
              paragraphs; when listing several items, put each on its own line starting with '- '."
        ),
        tools=[],
        llm=llm,
    )

    logger.info("Composer agent created")
    return agent
