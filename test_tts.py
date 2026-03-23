import requests
import os
from dotenv import load_dotenv

load_dotenv("c:/Users/eshaa/OneDrive/Desktop/hookfish voice agent/.env")
headers = {
    "xi-api-key": os.getenv("ELEVEN_API_KEY"),
    "Content-Type": "application/json"
}
voice_id = "MjJrIRgwH0lZCuxcakAW"
text = "नमस्ते, कैसे हैं आप?"

out = ""
def test_tts(model_id, language=None):
    global out
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": model_id
    }
    if language:
        payload["language_code"] = language
        
    try:
        r = requests.post(url, headers=headers, json=payload)
        content_type = r.headers.get("Content-Type", "")
        if r.status_code == 200 and "audio" in content_type:
            out += f"SUCCESS: model={model_id}, language={language}. Generated {len(r.content)} bytes.\n"
        else:
            out += f"FAILED: model={model_id}, language={language}. Status: {r.status_code}, Response: {r.text}\n"
    except Exception as e:
        out += f"ERROR: {e}\n"

test_tts("eleven_multilingual_v2", None)
test_tts("eleven_multilingual_v2", "hi")
test_tts("eleven_turbo_v2_5", None)
test_tts("eleven_turbo_v2_5", "hi")

with open("out3.txt", "w", encoding="utf-8") as f:
    f.write(out)
