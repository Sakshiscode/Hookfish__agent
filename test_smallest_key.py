import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("SMALLEST_API_KEY", "")
print(f"Testing API Key: {api_key[:10]}...")

# This is the actual Smallest AI API endpoint to test the key
url = "https://waves-api.smallest.ai/api/v1/lightning/get_tts"
payload = {
    "text": "Namaste, main Hookfish se bol rahi hoon.",
    "voice_id": "diya",
    "add_wav_header": True
}
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

try:
    response = requests.post(url, json=payload, headers=headers)
    print("Status Code:", response.status_code)
    if response.status_code != 200:
        print("Error Details:", response.text)
    else:
        print("Success! The API key is fully valid and audio was generated.")
except Exception as e:
    print("Failed to reach API:", e)
