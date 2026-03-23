import requests
import os
from dotenv import load_dotenv

load_dotenv("c:/Users/eshaa/OneDrive/Desktop/hookfish voice agent/.env")
headers = {
    "xi-api-key": os.getenv("ELEVEN_API_KEY"),
    "Content-Type": "application/json"
}
voice_id = "cgSgspJ2msm6clMCkdW9" # Jessica
text = "नमस्ते, मैं Hookfish से रिया बोल रही हूँ। हमारे पास एक नई प्रॉपर्टी listing आई है जो आपके clients के लिए काफी अच्छी हो सकती है। क्या आप 2 मिनट बात कर सकते हैं?"

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

with open("out4_utf8.txt", "w", encoding="utf-8") as f:
    f.write(out)
