import os, requests
from dotenv import load_dotenv
load_dotenv()

region = os.getenv("AZURE_SPEECH_REGION")
key = os.getenv("AZURE_SPEECH_KEY")

url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
headers = {"Ocp-Apim-Subscription-Key": key}
response = requests.get(url, headers=headers)
if response.status_code == 200:
    voices = response.json()
    indian_voices = [v for v in voices if "IN" in v["Locale"] and v["Gender"] == "Female"]
    print(f"Total Indian Female Voices Found: {len(indian_voices)}")
    for v in indian_voices:
        print(f"- {v['ShortName']} (Styles: {v.get('StyleList', 'None')})")
else:
    print("Error fetching voices:", response.text)
