from lib.agents import create_coordinator_agent

class Chatbot():
    def start_chatbot(self):
        print("Wiadomość powitalna")

        while(True):
            text = input()
            coordinator = create_coordinator_agent()
            response = coordinator.invoke({
                "messages": [("user", text)]
            })
            print(response["messages"][-1].content)