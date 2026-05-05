import asyncio
import os
import wave
import aiohttp
from dotenv import load_dotenv

from livekit.plugins.smallestai import TTS

load_dotenv()

async def main():
    api_key = os.getenv("SMALLEST_API_KEY")
    if not api_key:
        print("Error: SMALLEST_API_KEY not found in environment!")
        return

    # Initialize Smallest AI TTS plugin
    # Parameters like model, voice_id can be specified.
    # We must provide our own http_session when using outside of LiveKit Agent Context
    session = aiohttp.ClientSession()
    tts = TTS(http_session=session, model="lightning", voice_id="diya")
    
    text = "Hello there! I am generating this audio using the Smallest AI LiveKit plugin in Python."
    print("Generating audio for:", text)
    
    # synthesize method generally returns a ChunkedStream in livekit agents
    stream = tts.synthesize(text)
    
    frames = []
    
    # Iterating over the synthesized audio chunks
    async for audio in stream:
        if audio.frame:
            frames.append(audio.frame)
            
    print(f"Generated {len(frames)} audio frames.")
    
    await session.close()
    
    if not frames:
        print("No audio generated. Check your API key and parameters.")
        return

    # Extract format info from the first frame
    sample_rate = frames[0].sample_rate
    num_channels = frames[0].num_channels

    output_filename = "smallest_ai_output.wav"
    print(f"Saving to {output_filename}...")
    
    with wave.open(output_filename, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(2) # 16-bit PCM which is typical for LiveKit AudioFrame
        wav_file.setframerate(sample_rate)
        for frame in frames:
            wav_file.writeframes(frame.data)

    print("Successfully created the audio file!")

if __name__ == "__main__":
    asyncio.run(main())
