import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

try:
    print("Testing GROQ LLM...")
    # Trying the model from the code
    model = "meta-llama/llama-4-scout-17b-16e-instruct"
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Hi"}],
    )
    print("SUCCESS: Groq responded with:", response.choices[0].message.content)
except Exception as e:
    print(f"FAILED with {model}: {e}")
    
    # Fallback check
    print("\nTrying fallback model llama-3.1-8b-instant...")
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "Hi"}],
        )
        print("SUCCESS with fallback:", response.choices[0].message.content)
    except Exception as e2:
        print(f"FAILED fallback: {e2}")
