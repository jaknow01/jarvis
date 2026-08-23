from lib.runtime import Chatbot
from dotenv import load_dotenv
import asyncio
from lib.core import Logger
from lib.runtime import setup_tracing
import logging

logger = logging.getLogger(__name__)

def main():
    load_dotenv()
    Logger.config_root_logger()
    setup_tracing()
    logger.info("Initializing chatbot")

    chatbot = Chatbot()
    
    asyncio.run(chatbot.start_chatbot())

if __name__ == "__main__":
    main()