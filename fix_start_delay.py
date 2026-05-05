import re

with open('voice_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the start greeting block
bad_block = '''            async def trigger_greeting():
                await asyncio.sleep(0.5)
                logger.info("Agent generating initial outbound greeting...")
                c_name = caller_name if 'caller_name' in locals() and caller_name else "सर/मैडम"
                await voice_agent.session.generate_reply(
                    instructions=f"Say EXACTLY this and nothing else: 'नमस्ते {c_name} जी, कैसे हैं आप?'. Then STOP and wait for them to respond. Do NOT introduce yourself or the project yet."
                )
            asyncio.create_task(trigger_greeting())'''

good_block = '''            async def trigger_greeting():
                logger.info("Agent playing initial outbound greeting directly via TTS...")
                c_name = caller_name if 'caller_name' in locals() and caller_name else "सर/मैडम"
                try:
                    await voice_agent.session.say(f"नमस्ते {c_name} जी, कैसे हैं आप?", add_to_chat_ctx=True)
                except AttributeError:
                    # Fallback if say doesn't exist or isn't async
                    import livekit.agents.llm as llm_model
                    voice_agent.session.chat_ctx.messages.append(llm_model.ChatMessage(role="assistant", content=f"नमस्ते {c_name} जी, कैसे हैं आप?"))
                    await voice_agent.session.generate_reply()
            asyncio.create_task(trigger_greeting())'''

if bad_block in content:
    content = content.replace(bad_block, good_block)
    with open('voice_agent.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Start delay fixed securely!")
else:
    print("Bad block not found natively. We need regex.")
    
