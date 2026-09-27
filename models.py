import os, requests
from dotenv import load_dotenv
load_dotenv()

r = requests.get("https://api.groq.com/openai/v1/models",
                 headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY')}"})
print(r.status_code)
for m in r.json().get("data", []):
    print(m["id"])