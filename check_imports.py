import io

with open('voice_agent.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()
for line in lines:
    if "from livekit" in line or "import livekit" in line:
        print(line.rstrip())
