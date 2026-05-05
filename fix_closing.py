with open('voice_agent.py', 'rb') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    if i == 261:  # right after '--- END BROKER FLOW ---' (which is on line 262 of current file)
        new_lines.append(b'"""\n')

with open('voice_agent.py', 'wb') as f:
    f.writelines(new_lines)

print("Closing quotes inserted.")
