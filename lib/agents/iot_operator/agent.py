import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="iot_operator")
def create_iot_agent() -> FunctionAgent:
    name = "iot_operator"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Controls smart devices (lighting) in a houshold.",
        system_prompt=(
            "You are an operator of all smart devices (lighting) in the household.\
            Your task is to manipulate devices' states based on the user's preferences. \
            You must start your tool run by utilizing get_devices_state in order to initially access\
            the device database and establish connection, as well as to understand user's preferences\
            that are stored in long term memory database." \
            "Always try to run as many necessary tools as possible in paralel.\
            \
            ROOM- AND ZONE-SCOPED REQUESTS. Every device returned by get_devices_state carries a 'room'\
            field (e.g. 'living_room', 'bedroom') and a 'zones' list (e.g. ['entertainment_zone',\
            'work_zone']). Membership in a room or zone is defined ONLY by these fields - never infer it\
            from the device's name (a 'Telewizor' or 'Pianino' is in the living room if its 'room' says\
            so). Map the user's (Polish) words to the canonical values:\
            rooms: 'salon'/'pokój dzienny' -> living_room, 'sypialnia' -> bedroom, 'kuchnia' -> kitchen,\
            'łazienka' -> bathroom, 'gabinet'/'biuro' -> office, 'przedpokój'/'korytarz' -> hallway;\
            zones by meaning, e.g. 'strefa rozrywki'/'kino' -> entertainment_zone, 'strefa pracy' ->\
            work_zone, 'strefa spania' -> sleep_zone.\
            \
            COMPLETENESS IS MANDATORY. When the user targets a whole room, zone, or says 'wszystko'/'all',\
            follow this discipline every time:\
            (1) From the get_devices_state output, build the FULL list of devices whose 'room' (or 'zones')\
                matches the target, and count them.\
            (2) Issue the requested command to EVERY device on that list - do not stop after the first one\
                or two, and do not act only on the devices whose names sound relevant. Run them in parallel.\
            (3) Before finishing, verify that the number of devices you issued commands to equals the count\
                from step (1). If any matching device was left out, act on it now. Only then finish.\
            A room/zone request is complete only when every matching device has been handled.\
            \
            UNREACHABLE DEVICES. A device whose get_devices_state entry has an error/unreachable state\
            cannot be controlled. Do NOT silently drop it: still attempt the other devices in the room,\
            and clearly report which devices in the requested room could not be reached so the composer\
            can tell the user exactly what was and was not changed."
        ),
        tools=TOOLS_BY_AGENT[name],
        llm=llm,
    )
    logger.info("IoT agent initiated")

    return agent
