with open('voice_agent.py', 'rb') as f:
    content = f.read()

target = b'- NEVEBUYER_INSTRUCTIONS = """\r\n'
if target not in content:
    target = b'- NEVEBUYER_INSTRUCTIONS = """\n'

new_content = content.replace(target, b'"""\n\nBUYER_INSTRUCTIONS = """\n')

with open('voice_agent.py', 'wb') as f:
    f.write(new_content)

print("Replacement successful." if target in content else "Target not found.")
