import io

with open('voice_agent.py', 'rb') as f:
    for i, line in enumerate(f):
        try:
            line.decode('utf-8')
        except UnicodeDecodeError as e:
            print(f"Line {i+1} has invalid utf-8:")
            print(repr(line))
