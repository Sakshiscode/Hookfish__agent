import asyncio
from livekit.plugins import groq
from dotenv import load_dotenv
import os

load_dotenv()

async def test():
    try:
        llm = groq.LLM(model='llama-3.3-70b-versatile')
        print(f"Testing llama-3.3-70b-versatile...")
        # chat returns an AsyncIterable in latest livekit-agents
        stream = await llm.chat(prompt="Hi")
        async for chunk in stream:
            print(chunk.choices[0].delta.content or "", end="")
        print("\nSUCCESS")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test())
