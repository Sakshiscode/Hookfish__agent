import urllib.request
import json
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("CEREBRAS_API_KEY")
print(f"API key starts with: {api_key[:10]}..." if api_key and len(api_key) > 10 else f"API key: {api_key}")

try:
    req = urllib.request.Request(
        "https://api.cerebras.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    print("\nAvailable models:")
    for m in data["data"]:
        print(f"  - {m['id']}")
except Exception as e:
    print(f"\nError: {e}")
