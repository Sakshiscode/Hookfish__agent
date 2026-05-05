with open('voice_agent.py', 'rb') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    # lines 263 to 284 (inclusive) are indices 262 to 283
    if not (262 <= i <= 283):
        new_lines.append(line)

with open('voice_agent.py', 'wb') as f:
    f.writelines(new_lines)

print("Duplicates removed.")
