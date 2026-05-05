with open('voice_agent.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 380 <= i <= 410:
            print(f"{i}: {line.rstrip()}")
