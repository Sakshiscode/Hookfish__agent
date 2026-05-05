import asyncio
import os
import aiohttp
from dotenv import load_dotenv
from livekit.plugins.smallestai import TTS

load_dotenv()

async def test_devanagari():
    api_key = os.getenv("SMALLEST_API_KEY")
    print(f"Testing Smallest AI with Devanagari script...")
    
    async with aiohttp.ClientSession() as session:
        # Replicating the config in voice_agent.py
        tts = TTS(
            http_session=session, 
            model="lightning-v2", 
            voice_id="voice_rMGxcmm5RI",
            language="hi"
        )
        
        try:
            print("Synthesizing...")
            # Using the same text the agent would start with
            text = "नमस्ते! मैं रिया बोल रही हूँ। एक मिनट है आपके पास?"
            stream = tts.synthesize(text)
            
            frame_count = 0
            async for audio in stream:
                if audio.frame:
                    frame_count += 1
            
            print(f"SUCCESS: Generated {frame_count} audio frames.")
        except Exception as e:
            print(f"FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_devanagari())
