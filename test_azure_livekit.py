import os
from dotenv import load_dotenv
import time
import asyncio
from livekit.plugins import openai
from livekit.agents.llm import ChatContext
from livekit.agents.llm import ChatMessage
import logging
logging.basicConfig(level=logging.INFO)

load_dotenv()

async def main():
    print("Testing livekit openai.LLM.with_azure...")
    llm = openai.LLM.with_azure(
        azure_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
        api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
        api_version="2024-05-01-preview",
    )
    
    chat_ctx = ChatContext()
    chat_ctx.messages.append(ChatMessage(role="user", content="Hello"))
    
    start = time.time()
    try:
        response = await llm.chat(chat_ctx=chat_ctx)
        async for chunk in response:
            pass # just consume
        print(f"Time taken to get completion stream: {time.time() - start} seconds")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
