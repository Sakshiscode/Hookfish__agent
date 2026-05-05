with open('voice_agent.py', 'rb') as f:
    content = f.read()

target = b'"""\xa5\x87 \xe0\xa4\xb9BROKER_INSTRUCTIONS = """\r\n'
new_content = content.replace(target, b'"""\n\nBROKER_INSTRUCTIONS = """\n')

with open('voice_agent.py', 'wb') as f:
    f.write(new_content)

print("Replacement successful." if target in content else "Target not found.")
