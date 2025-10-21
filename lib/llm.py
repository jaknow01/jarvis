from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
import os

LLM_BY_AGENT: dict[str: BaseChatModel] = {}

def llm_usage(agent_names: list[str]):
    def wrapper(func):
        for agent in agent_names:
            LLM_BY_AGENT[agent] = func
        return func
    return wrapper

@llm_usage(["coordinator"])
def open_ai_llm() -> ChatOpenAI:
    llm = ChatOpenAI(
        model = os.getenv("OPENAI_DEFAULT_MODEL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        output_version="responses/v1",
        use_previous_response_id=True
    )
    return llm