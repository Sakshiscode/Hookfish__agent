import re

with open('voice_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = 'stt=deepgram.STT(model="nova-2", language="hi", smart_format=False, endpointing_ms=100)'
replacement = 'stt=deepgram.STT(model="nova-2", language="hi", interim_results=True, smart_format=False)'

content = content.replace(target, replacement)

# Let's fix the STT completely removing endpointing_ms
if "endpointing_ms" in content:
    content = re.sub(r'endpointing_ms=\d+', '', content)

with open('voice_agent.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("STT delay fixed")
