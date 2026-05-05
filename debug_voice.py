import asyncio
import os
import wave
import aiohttp
from dotenv import load_dotenv

from livekit.plugins.smallestai import TTS

load_dotenv()

async def list_voices(session):
    # This is a guestimate based on standard LiveKit plugin patterns
    # and Smallest AI API. I'll check what models/voices are valid.
    pass

async def test_voice(model, voice_id):
    print(f"\n--- Testing Model: {model}, Voice: {voice_id} ---")
    async with aiohttp.ClientSession() as session:
        tts = TTS(http_session=session, model=model, voice_id=voice_id)
        try:
            stream = tts.synthesize("Hello, testing this specific voice ID.")
            frames = []
            async for audio in stream:
                if audio.frame:
                    frames.append(audio.frame)
            print(f"SUCCESS: Generated {len(frames)} frames.")
        except Exception as e:
            print(f"FAILED: {e}")

async def main():
    # The user provided voice_rMGxcmm5RI
    # Let's try with 'lightning' (currently in voice_agent.py)
    await test_voice("lightning", "voice_rMGxcmm5RI")
    
    # Let's try with 'lightning-v2' if available
    await test_voice("lightning-v2", "voice_rMGxcmm5RI")
    
    # Let's try with 'lightning-large'
    await test_voice("lightning-large", "voice_rMGxcmm5RI")

if __name__ == "__main__":
    asyncio.run(main())
