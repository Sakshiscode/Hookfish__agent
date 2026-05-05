import re

with open('voice_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Optimize Deepgram STT
content = content.replace(
    'stt=deepgram.STT(model="nova-2", language="hi"),',
    'stt=deepgram.STT(model="nova-2", language="hi", smart_format=False, endpointing_ms=100),'
)

# 2. Optimize Azure LLM Tools
content = content.replace(
    'api_version="2024-05-01-preview",',
    'api_version="2024-05-01-preview",\n                parallel_tool_calls=False,'
)

# 3. Optimize AgentSession endpointing
content = content.replace(
    'min_endpointing_delay=0.1,',
    'min_endpointing_delay=0.05,'
)
content = content.replace(
    'max_endpointing_delay=0.3,',
    'max_endpointing_delay=0.15,'
)

with open('voice_agent.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Mid-conversation latency fixes applied!")
