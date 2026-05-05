import asyncio
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()

async def test_native_hi():
    api_key = os.getenv("SMALLEST_API_KEY")
    url = "https://waves-api.smallest.ai/api/v1/lightning-v2/get_speech"
    
    data = {
        "voice_id": "voice_rMGxcmm5RI",
        "text": "नमस्ते! मैं रिया बोल रही हूँ। एक मिनट है आपके पास?",
        "sample_rate": 24000,
        "speed": 1.0,
        "language": "hi",
        "output_format": "pcm"
    }
    
    print(f"Testing Smallest AI Native hi-IN...")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=data,
                timeout=aiohttp.ClientTimeout(total=30) # Giving more time
            ) as resp:
                print(f"Status: {resp.status}")
                if resp.status == 200:
                    print("SUCCESS!")
                else:
                    text = await resp.text()
                    print(f"ERROR: {text}")
        except Exception as e:
            print(f"EXCEPTION: {e}")

if __name__ == "__main__":
    asyncio.run(test_native_hi())
