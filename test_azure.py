import os
from dotenv import load_dotenv
import time
import asyncio
from openai import AsyncAzureOpenAI

load_dotenv()

async def main():
    print("Testing standard AsyncAzureOpenAI client chat completion...")
    client = AsyncAzureOpenAI(
        api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
        api_version="2024-05-01-preview",
    )
    
    start = time.time()
    try:
        response = await client.chat.completions.create(
            model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        print(f"Time taken to get completion: {time.time() - start} seconds")
        print(response.choices[0].message.content)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
