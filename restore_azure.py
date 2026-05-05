import re

with open('voice_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove parallel_tool_calls=False which causes HTTP 400 on Azure 2024-05-01-preview
target = 'api_version="2024-05-01-preview",\n                parallel_tool_calls=False,'
replacement = 'api_version="2024-05-01-preview",'

if target in content:
    content = content.replace(target, replacement)
    with open('voice_agent.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Fixed Azure API compatibility (removed parallel_tool_calls=False).")
else:
    # Use regex just in case
    new_content = re.sub(r'parallel_tool_calls=False,?\s*', '', content)
    with open('voice_agent.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Fixed Azure API compatibility (via regex).")
