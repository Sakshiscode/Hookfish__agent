import re

with open('voice_agent.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# 1. Replace FilteredTTS with smallestai.TTS
content = content.replace("tts=FilteredTTS(", "tts=smallestai.TTS(")

# 2. Add silero VAD initialization in super().__init__(
vad_string_to_add = """
            vad=silero.VAD.load(min_silence_duration=0.1, min_speech_duration=0.05),"""

# Find where super().__init__( is called for VoiceAgent
match = re.search(r'super\(\)\.__init__\(\s*', content)
if match:
    insert_pos = match.end()
    # verify we haven't already added vad
    if 'vad=silero' not in content[insert_pos:insert_pos+200]:
        content = content[:insert_pos] + vad_string_to_add + content[insert_pos:]

# 3. Ensure silero is imported
if 'import silero' not in content:
    content = content.replace("from livekit.plugins import ", "from livekit.plugins import silero, ")

with open('voice_agent.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Latency optimizations applied!")
