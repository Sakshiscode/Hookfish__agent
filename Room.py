import requests

LIVEKIT_URL=wss://hookfish-wkzzei57.livekit.cloud
LIVEKIT_API_KEY=APIGTxnen7Zg4TD
LIVEKIT_API_SECRET=xwmDVZd6XEf7u0ZJ3LLnUjGtoZmz41KMQQUEobzXwNC

# Create a room
response = requests.post(
    f"{LIVEKIT_URL}/api/rooms",
    auth=(API_KEY, API_SECRET),
    json={"name": "test-room"}
)

print(response.json())
