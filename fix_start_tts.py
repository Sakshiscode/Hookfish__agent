import re

with open('voice_agent.py', 'r', encoding='utf-8') as f:
    text = f.read()

target = 'await voice_agent.session.say(f"नमस्ते {c_name} जी, कैसे हैं आप?", add_to_chat_ctx=True)'

replacement = '''# Stream in chunks for minimal TTS time-to-first-byte
                    await voice_agent.session.say(f"नमस्ते {c_name} जी,", add_to_chat_ctx=True)
                    await voice_agent.session.say("कैसे हैं आप?", add_to_chat_ctx=True)'''

if target in text:
    text = text.replace(target, replacement)
    with open('voice_agent.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("Split TTS greeting to minimize TTFB!")
else:
    print("Could not find the exact say statement")

