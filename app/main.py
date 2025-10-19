from lib.chatbot import Chatbot
from dotenv import load_dotenv

def main():
    load_dotenv()
    chatbot = Chatbot()
    chatbot.start_chatbot()

if __name__ == "__main__":
    main()