import os
import asyncio
from dotenv import load_dotenv
load_dotenv()

from livekit.plugins import smallestai

async def main():
    try:
        tts = smallestai.TTS(voice_id='diya')
        print("Synthesizing...", flush=True)
        audio_stream = tts.synthesize("Namaste, main Hookfish se bol rahi hoon.")
        
        chunk_count = 0
        async for chunk in audio_stream:
            chunk_count += 1
            print(f"Received chunk {chunk_count}: {len(chunk.data)} bytes", flush=True)
            
        print("Success! Audio generated.", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
