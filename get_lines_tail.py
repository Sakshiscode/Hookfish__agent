import io

with open('voice_agent.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

with io.open('get_lines_tail.txt', 'w', encoding='utf-8') as out:
    for i, line in enumerate(lines):
        if i >= 270:
            out.write(f"Line {i+1}: {repr(line)}\n")
