with open('voice_agent.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 392 <= i <= 410:
        print(f"Line {i+1}: {line.rstrip()}")
