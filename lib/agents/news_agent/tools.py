"""news_agent tool: news / market-news search via Tavily. Registered into
TOOLS_BY_AGENT["news_agent"] via @tool_ownership."""

import logging
import os
from typing import Literal

from tavily import TavilyClient
from llama_index.core.workflow import Context

from lib.core import tool_ownership, function_tool, app_context

logger = logging.getLogger(__name__)


@tool_ownership("news_agent")
@function_tool
async def search_news(ctx: Context,
                               query: str,
                               topic: Literal["news", "finance"],
                               search_depth: Literal["basic", "advanced"] = "advanced"
                               ) -> dict:
    """
    Description:
        This tool is used to crawl through varius reputable news sources in search for current and historical events to
        find an answer to the user's query.

    Parameters:
    ctx : Context
        Context in which the tool operates

    query: str
        Defines the matter or event that interests the user. Should be structured in natural language
        and reflect exactly what the user wants to know.
        For better search results this parameter
        **must** be written entirely in english as it's the most optimal language for the search engine

    topic: Literal["news", "finance"]
        Controls what is the general topic of the user's query. All political, sports, history-related etc. queries should be designated as 'news'
        wheras all stock market, commodities, currencies, cryptocurrencies related queries etc. should be treated as 'finance'

    search_depth: Literal["basic", "advanced"] = "advanced"
        The level of detail that should be extracted by the search engine.
        Value 'basic' should be used when user requests a quick and short summary - should only
        be used when user clearly asks for a short anwser.
        Value 'advanced' is the default value which provides more details.
    """
    logging.info(f"Starting news search with query {query} at {search_depth} depth")

    client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

    # general search
    response = client.search(
        query=query,
        include_answer="advanced",
        search_depth=search_depth,
        topic=topic
    )

    # reputable source
    response_reputable = client.search(
        query=query,
        include_answer="advanced",
        search_depth=search_depth,
        topic="news",
        include_domains=["reuters.com"] if topic == "news" else ["bloomberg.com"]
    )

    result = {
        "general_search": response,
        "reputable source": response_reputable
    }

    return result
