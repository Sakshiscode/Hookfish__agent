with open('voice_agent.py', 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        if "class VoiceAgent" in line or "from livekit.agents.pipeline import" in line or "Agent" in line:
            print(line.rstrip())
