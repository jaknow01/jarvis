from agents import RunContextWrapper, function_tool

TOOLS_BY_AGENT: dict[str: list[str]] = {}

def tool_ownership(agent_name: str):
    def wrapper(func):
        if agent_name in TOOLS_BY_AGENT:
            TOOLS_BY_AGENT[agent_name].append(func)
        else:
            TOOLS_BY_AGENT[agent_name] = [func]
        return func
    return wrapper

class Ctx():
    def __init__(self):
        # do zastanowienia się co mogę chcieć przekazywać między toolami tego samego agenta
        # właściwie to narzędzia każdego podagenta muszą mieć swoje
        pass

# @tool_ownership("coordinator")
# @function_tool
# def test():
#     """
#     Doesnt do anything
#     """
#     pass