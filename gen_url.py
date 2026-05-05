import os
import urllib.parse
from livekit import api
from dotenv import load_dotenv

load_dotenv()
token = api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")) \
    .with_identity("test-user") \
    .with_name("Test User") \
    .with_grants(api.VideoGrants(room_join=True, room="test-room")) \
    .to_jwt()

url = urllib.parse.quote(os.getenv("LIVEKIT_URL"))
print(f"https://agents-playground.livekit.io/?url={url}&token={token}")
