import re

with open('voice_agent.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Fix pronunciation of Mahim -> माहिम
text = re.sub(r'\bMahim\b', 'माहिम', text)
text = re.sub(r'\bMahim West\b', 'माहिम West', text)

# For "zero delay", disable preemptive generation which might be choking Azure/LiveKit async loop 
# with too many function-call-disabled incomplete streams when small pauses happen!
text = text.replace('preemptive_generation=True', 'preemptive_generation=False')

# Let's adjust max_endpointing_delay
text = text.replace('max_endpointing_delay=0.15', 'max_endpointing_delay=0.1')
text = text.replace('min_endpointing_delay=0.08', 'min_endpointing_delay=0.05')

# Attempt to configure VAD inside AgentSession as well
if 'aec_warmup_duration=0,' in text and 'turn_detector=vad,' not in text:
    text = text.replace('aec_warmup_duration=0,', 'aec_warmup_duration=0,\n        # pass vad explicitly to AgentSession to ensure local aggressive control overrides\n        turn_detector=silero.VAD.load(min_silence_duration=0.1, min_speech_duration=0.05),')

with open('voice_agent.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Done fixing Mahim and turning off preemptive LLM to avoid async queuing delays")
