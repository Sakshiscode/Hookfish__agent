import asyncio
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()

async def test_endpoint(model, voice_id):
    api_key = os.getenv("SMALLEST_API_KEY")
    base_url = "https://waves-api.smallest.ai/api/v1"
    
    # Mirroring the plugin logic
    endpoint = "get_speech_long_text"
    if model == "lightning-v2":
        endpoint = "get_speech"
        
    url = f"{base_url}/{model}/{endpoint}"
    
    data = {
        "voice_id": voice_id,
        "text": "Hello, this is a test of the smallest ai waves api.",
        "sample_rate": 24000,
        "speed": 1.0,
        "language": "en",
        "output_format": "pcm"
    }
    
    if model != "lightning":
        data.update({
            "consistency": 0.5,
            "similarity": 0,
            "enhancement": 1
        })
        
    print(f"\n--- Testing Model: {model} ---")
    print(f"URL: {url}")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=data
            ) as resp:
                status = resp.status
                print(f"Status: {status}")
                if status == 200:
                    print("SUCCESS!")
                else:
                    text = await resp.text()
                    print(f"ERROR BODY: {text}")
        except Exception as e:
            print(f"EXCEPTION: {e}")

async def main():
    voice_id = "voice_rMGxcmm5RI"
    models = ["lightning", "lightning-large", "lightning-v2"]
    for m in models:
        await test_endpoint(m, voice_id)

if __name__ == "__main__":
    asyncio.run(main())
