import asyncio
import os
import aiohttp
from dotenv import load_dotenv

from livekit.plugins.smallestai import TTS

load_dotenv()

async def main():
    async with aiohttp.ClientSession() as session:
        tts = TTS(http_session=session, model="lightning", voice_id="diya")
        try:
            print("Testing lightning/diya...")
            stream = tts.synthesize("Namaste, main Hookfish se bol rahi hoon.")
            async for audio in stream:
                pass
            print("SUCCESS: lightning/diya generated audio.")
        except Exception as e:
            print(f"FAILED: lightning/diya: {e}")

if __name__ == "__main__":
    asyncio.run(main())
