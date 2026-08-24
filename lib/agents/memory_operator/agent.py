import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="memory_operator")
def create_memory_agent() -> FunctionAgent:
    name = "memory_operator"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Stores, retrieves and updates the user's long-term preferences and facts.",
        system_prompt=(
            "You manage the user's long-term memory: durable preferences, facts, habits, interests and routines.\
            Use get_memory to look things up, save_memory to store a new durable preference or fact\
            (set source='user' when the user stated it explicitly, 'inferred' when you concluded it),\
            update_memory to correct an existing entry, and delete_memory to remove one.\
            Store concise, self-contained statements in natural language. Suggested categories:\
            preferences, facts, habits, interests, routines. Never store transient or one-off details,\
            secrets, or credentials."
        ),
        tools=TOOLS_BY_AGENT[name],
        llm=llm,
    )

    logger.info("Memory agent created")
    return agent
