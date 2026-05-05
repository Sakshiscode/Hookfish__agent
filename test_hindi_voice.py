import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("SMALLEST_API_KEY", "")
print(f"Testing API Key: {api_key[:10]}...")

url = "https://waves-api.smallest.ai/api/v1/lightning/get_tts"

# Test both: Devanagari Hindi vs Romanized Hinglish
samples = {
    "devanagari_hindi": "नमस्ते, मैं हुकफिश से रिया बोल रही हूँ। आपको एक प्रॉपर्टी के बारे में कॉल किया था। क्या आपके पास एक मिनट है?",
    "romanized_hinglish": "Namaste, main Hookfish se Riya bol rahi hoon. Aapko ek property ke baare mein call kiya tha. Kya aapke paas ek minute hai?",
}

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

for label, text in samples.items():
    print(f"\n--- Generating: {label} ---")
    print(f"Text: {text}")
    
    payload = {
        "text": text,
        "voice_id": "voice_BTq3OaiWFN",
        "add_wav_header": True,
        "language": "hi",
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            filename = f"sample_{label}.wav"
            with open(filename, "wb") as f:
                f.write(response.content)
            print(f"Saved: {filename} ({len(response.content)} bytes)")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Failed: {e}")

print("\n✅ Done! Play both .wav files to compare.")
