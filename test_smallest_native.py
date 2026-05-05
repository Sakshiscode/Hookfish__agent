import os
from smallestai import Smallest
from dotenv import load_dotenv

load_dotenv()
client = Smallest(api_key=os.getenv("SMALLEST_API_KEY"))

try:
    print("Sending native request to Smallest.ai...")
    response = client.waves.generate(text="hello world", voice_id="diya", model="lightning")
    with open("test.wav", "wb") as f:
        f.write(response)
    print("Success! Native client generated audio.")
except Exception as e:
    import traceback
    traceback.print_exc()
