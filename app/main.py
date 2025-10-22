from lib.chatbot import Chatbot
from dotenv import load_dotenv
import asyncio

def main():
    load_dotenv()
    chatbot = Chatbot()
    asyncio.run(chatbot.start_chatbot())

if __name__ == "__main__":
    main()