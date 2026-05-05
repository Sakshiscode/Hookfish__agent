import os, asyncio, time
from dotenv import load_dotenv
load_dotenv()
import sys; sys.path.insert(0,'.')
from openai import AsyncAzureOpenAI

async def main():
    # Load the ACTUAL system prompt
    from voice_agent import build_agent_instructions
    actual_prompt = build_agent_instructions(True, '+916362185137', 'broker', 'TestUser')
    print(f"System prompt size: {len(actual_prompt)} chars, ~{len(actual_prompt)//4} tokens")
    
    client = AsyncAzureOpenAI(
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT",""),
        api_key=os.environ.get("AZURE_OPENAI_API_KEY",""),
        api_version="2024-05-01-preview",
    )

    # Run 3 rounds
    for i in range(3):
        print(f"\n--- Round {i+1} ---")
        start = time.time()
        resp = await client.chat.completions.create(
            model=os.environ.get("AZURE_OPENAI_DEPLOYMENT","gpt-4o"),
            messages=[
                {"role":"system","content": actual_prompt},
                {"role":"user","content":"Hello? Haan boliye"}
            ],
            stream=True,
            max_tokens=100,
        )
        first=True
        full=""
        async for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                if first:
                    print(f"  TTFT: {time.time()-start:.3f}s")
                    first=False
                full += chunk.choices[0].delta.content
        print(f"  Total: {time.time()-start:.3f}s")
        print(f"  Response: {full[:80]}...")

asyncio.run(main())
