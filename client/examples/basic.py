from science_assistant_client import ScienceAssistantClient

client = ScienceAssistantClient()
print(client.info()["package"])
