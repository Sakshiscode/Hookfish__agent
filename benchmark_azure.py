import os, asyncio, time
from dotenv import load_dotenv
load_dotenv()
from openai import AsyncAzureOpenAI

async def main():
    client = AsyncAzureOpenAI(
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT",""),
        api_key=os.environ.get("AZURE_OPENAI_API_KEY",""),
        api_version="2024-05-01-preview",
    )

    # Test 1: Simple short prompt (like a greeting response)
    print("=== Test 1: Short prompt ===")
    start = time.time()
    resp = await client.chat.completions.create(
        model=os.environ.get("AZURE_OPENAI_DEPLOYMENT","gpt-4o"),
        messages=[
            {"role":"system","content":"Reply in 1 short sentence in Hindi."},
            {"role":"user","content":"Hello?"}
        ],
        stream=True,
        max_tokens=50,
    )
    first=True
    async for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            if first:
                print(f"  TTFT: {time.time()-start:.3f}s")
                first=False
    print(f"  Total: {time.time()-start:.3f}s")

    # Test 2: Large system prompt (simulating voice agent)
    print("\n=== Test 2: Large system prompt (like voice agent) ===")
    big_prompt = "You are Riya from Hookfish. " * 200  # ~4000 tokens
    start = time.time()
    resp = await client.chat.completions.create(
        model=os.environ.get("AZURE_OPENAI_DEPLOYMENT","gpt-4o"),
        messages=[
            {"role":"system","content":big_prompt},
            {"role":"user","content":"Hello?"}
        ],
        stream=True,
        max_tokens=50,
    )
    first=True
    async for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            if first:
                print(f"  TTFT: {time.time()-start:.3f}s")
                first=False
    print(f"  Total: {time.time()-start:.3f}s")

asyncio.run(main())
