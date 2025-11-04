from lib.chatbot import Chatbot
from dotenv import load_dotenv
import asyncio
from lib.logger import Logger
import logging

logger = logging.getLogger(__name__)

def main():
    load_dotenv()
    Logger.config_root_logger()
    logger.info("Initializing chatbot")

    chatbot = Chatbot()
    
    asyncio.run(chatbot.start_chatbot())

if __name__ == "__main__":
    main()