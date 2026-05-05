import os
import asyncio
from livekit import api
from dotenv import load_dotenv

load_dotenv()

async def trigger():
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )
    await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name="hookfish-voice-agent",
            room="test-room",
        )
    )
    await lkapi.aclose()

asyncio.run(trigger())
