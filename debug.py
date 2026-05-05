import io

with open('voice_agent.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()[170:194]

with io.open('tmp_lines.txt', 'w', encoding='utf-8') as out:
    for i, line in enumerate(lines):
        out.write(f"{i+171}: {repr(line)}\n")
