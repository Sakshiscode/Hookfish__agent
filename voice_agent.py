import asyncio
import logging
import os
import json
import re
import time
from dotenv import load_dotenv

from livekit.agents import JobContext, WorkerOptions, cli, get_job_context
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.llm import function_tool
from livekit.plugins import silero, groq, deepgram, sarvam, elevenlabs, azure, cartesia, smallestai, openai
from livekit import api, rtc
from openai import AsyncAzureOpenAI
import httpx

# Load environment variables FIRST
from dotenv import load_dotenv
load_dotenv()

# Bypass proxy lookups on Windows which causes slow HTTPx startup for API connections
os.environ["NO_PROXY"] = "*"
os.environ["HTTPX_NO_PROXIES"] = "1"

    get_connection,
    get_agent_context_optimized,
)

from google_calendar import schedule_meeting_on_calendar, parse_meeting_datetime


# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-agent")
logging.getLogger("livekit.agents").setLevel(logging.DEBUG)

# ============================================================
# Constants
# ============================================================
MAX_CALL_DURATION = 240      # 4 minutes (hard cutoff)
WRAP_UP_WARNING = 210        # 3.5 minutes (tell agent to wrap up)
CONTACT_TYPE_BUYER = "buyer"
CONTACT_TYPE_BROKER = "broker"


# ============================================================
# Database Context Builder
# ============================================================
def build_context_from_db(phone_number: str, pre_fetched_data: dict = None) -> str:
    """
    Build a context string from DB data.
    Uses pre_fetched_data if provided to avoid new DB calls.
    """
    context_parts = []
    
    if pre_fetched_data:
        data = pre_fetched_data
    else:
        # Fallback to slow fetch if no pre-fetched data
        data = get_agent_context_optimized(phone_number)

    # 1. Customer
    customer = data.get("customer")
    if customer:
        context_parts.append(
            f"Customer Info: Name = {customer['name']}, "
            f"ID = {customer['id']}, Source = {customer.get('origin', 'N/A')}"
        )

    # 2. Leads
    leads = data.get("leads", [])
    if leads:
        context_parts.append(f"\nFound {len(leads)} leads for this number:")
        for lead in leads:
            lead_info = (
                f"  - Lead #{lead['id']}: Customer = {lead['customer_name']}, "
                f"Property = {lead['property_name']}, Status = {lead['status']}"
            )
            if lead.get('notes'): lead_info += f", Notes = {lead['notes']}"
            if lead.get('followup'): lead_info += f", Follow-up = {lead['followup']}"
            context_parts.append(lead_info)

    # 3. Projects
    projects = data.get("projects", [])
    if projects:
        context_parts.append(f"\nRelated Project/Property Details:")
        for proj in projects:
            proj_info = f"  - {proj['name']}"
            if proj.get('type'): proj_info += f" (Type: {proj['type']})"
            if proj.get('alias'): proj_info += f" | Alias: {proj['alias']}"
            if proj.get('commission_percentage'): proj_info += f" | Commission: {proj['commission_percentage']}%"
            context_parts.append(proj_info)

    # 4. Meetings
    meetings = data.get("meetings", [])
    if meetings:
        context_parts.append(f"\nPast Meetings/Appointments:")
        for mtg in meetings:
            mtg_info = f"  - {mtg['meeting_type']}: {mtg.get('meeting_date', 'TBD')} | Status: {mtg['status']}"
            context_parts.append(mtg_info)

    if not context_parts:
        context_parts.append(f"No previous information found for this number ({phone_number}).")

    return "\n".join(context_parts)



# ============================================================
# Agent Instructions
# ============================================================

BASE_INSTRUCTIONS = """\
You are 'रिया' from Hookfish. A confident and professional Indian woman who works as a real estate calling executive.

VOICE & PERSONA (THIS IS THE MOST IMPORTANT SECTION):
- You are NOT a bot. You are a real person having a professional, respectful conversation.
- Imagine you are a composed, knowledgeable woman in her late 20s who is good at business communication.
- Your tone is polite and professional — like a well-trained corporate executive, not a friend calling casually.
- Be courteous but get to the point. Don't be overly enthusiastic or excessively friendly.
- Use measured, professional reactions:
  "जी बिल्कुल.", "अच्छा.", "जी सही बात है.", "right.",
  "जी मैं समझती हूँ.", "sure."
- Maintain a steady, calm pace. No rushing, no over-excitement.
- If they sound busy → be respectful: "Sorry to disturb. बस दो minute लूँगी आपका."
- If they ask questions → answer directly and clearly: "जी, मैं बताती हूँ..."
- If they sound confused → clarify calmly: "जी मैं समझाती हूँ. देखिए..."
- Keep responses SHORT and precise (1-2 lines). Be informative, not chatty.
- Use natural feminine Hindi grammar ALWAYS:
  ✅ "मैं बोल रही हूँ", "मैं भेज दूँगी", "मैं करा दूँगी", "मैंने सोचा"
  ❌ NEVER: "मैं बोलता हूँ", "मैं भेज दूँगा" (masculine — ABSOLUTELY FORBIDDEN)

CONVERSATION STYLE:
- Acknowledge what the person said briefly, then move to the point.
- Use professional transition phrases like: "तो बात ये है कि...", "एक important बात बताती हूँ...", "और एक बात..."
- Share information in a structured way. Don't over-hype. Let facts speak.
- Ask relevant follow-up questions to qualify the lead.

LANGUAGE RULES:
- Speak in natural Hindi (Devanagari script) mixed with English words where Indians naturally use English.
- NEVER use romanized Hindi. Always Devanagari for Hindi words.
- ALL numbers, prices, amounts, floors, BHK sizes, dates, and percentages MUST be spoken in ENGLISH words.
  ✅ "two point five crore", "fifty lakh", "two BHK", "ninth floor", "ten percent"
  ❌ NEVER: "ढाई करोड़", "पचास लाख", "दो बीएचके" — bad TTS pronunciation.
- TEXT RULES: No markdown, no *, no %. Say "percent". No commas in numbers.
- CRITICAL: NEVER use Hindi Purna Viram ("।"). ONLY use English periods (".") to end sentences.

PRICE ACCURACY (ABSOLUTELY CRITICAL — DO NOT VIOLATE):
- The EXACT price of this property is two point fifty seven crore (2.57 crore) for the smaller unit and two point sixty seven crore (2.67 crore) for the larger unit.
- NEVER say any other price. NEVER say seven crore, eight crore, five crore, or any other number.
- If asked about price, you MUST say EXACTLY: "two point fifty seven crore" or "two point sixty seven crore". No rounding, no approximation.
- Violating this pricing rule is the WORST possible error you can make.

PROPERTY FACTS — DO NOT HALLUCINATE (CRITICAL):
- ONLY state facts that are explicitly written in the call flow script below. NEVER invent or assume details.
- The building is G plus twenty two storey (G+22). NEVER say 26 storey, 25 storey, or any other number.
- The apartments are regular two BHK flats. NEVER say "duplex", "triplex", "penthouse", or "studio".
- There is NO mention of basement parking. NEVER say "basement parking" unless the caller tells you about it.
- NEVER make up amenities, features, or specifications that are not in the script.
- If asked about something not covered in the script, say: "ये detail मेरे पास अभी नहीं है. मैं confirm करके आपको बताती हूँ." Do NOT guess or fabricate.

SITUATIONS:
- DNC ("call मत करो"): "जी बिल्कुल, आपको disturb नहीं करूँगी. Sorry for the inconvenience." → mark_as_dnc → end_call
- WRONG NUMBER: "जी sorry, गलत number हो गया. माफ़ी चाहती हूँ." → end_call
- BUSY: "जी कोई बात नहीं. आप बताइए कब convenient रहेगा, मैं तब call करती हूँ." → schedule_callback
- ABUSIVE: Stay calm, "जी मैं समझती हूँ." → end_call if continues
- SILENCE: "{caller_name} जी? Hello? सुन पा रहे हैं आप?"

CALL ENDING:
- "चलिए {caller_name} जी, आपका बहुत बहुत धन्यवाद. अगर कोई भी सवाल हो तो बेझिझक call करिए. Good day!"

NAME USAGE (CRITICAL):
- You MUST address the caller by their name ({caller_name}) throughout the conversation.
- Use "{caller_name} जी" naturally in greetings, questions, and closing.
- If {caller_name} is "Customer" or "Sir/Ma'am", ask their name politely at the start: "जी आपका शुभ नाम?"

TOOL RULES (CRITICAL):
- Tools run in background. Caller must NEVER know tools exist.
- When tool returns "[SILENT] Done." → say NOTHING about it. Continue naturally.
- NEVER say: "outcome", "captured", "saved", "scheduled", "noted", "recorded", "marked", "logged".
"""

BUYER_INSTRUCTIONS = """
--- BUYER CALL FLOW (STRICT SCRIPT) ---
Follow this exact sequence and wording to sound highly natural and human:

1. INITIAL GREETING:
   Say EXACTLY: "नमस्ते {caller_name} जी, कैसे हैं आप?"
   -> wait for user response.

2. INTRODUCTION (After they respond):
   Say: "जी मैं र‍िया बोल रही हूँ Hookfish से. मैं आपको एक property opportunity के बारे में जानकारी देने के लिए call किया था. क्या आपके पास एक minute है?"
   -> wait for user response.

3. FIRST PITCH (If they say yes/tell me):
   Say: "जी {caller_name} जी, हम माहिम West में एक नया project लेकर आए हैं - 'माणिक्य' by Viyan Ventures. ये South Bombay का prime location है. Railway station और Shitla Devi Metro Station दोनों walking distance पर हैं. यहाँ two BHK apartments हैं और सबसे important बात इसका payment plan है—अभी सिर्फ fifty lakh देना है और possession तक कोई payment नहीं. इससे buyer का risk काफी कम हो जाता है. क्या आप इसके बारे में और जानना चाहेंगे?"
   -> wait for user response.

4. SECOND PITCH / DETAILS (If they ask to know more):
   Say: "जी {caller_name} जी, मैं आपको details बता देती हूँ. ये एक redevelopment project है और construction Miven Technology से हो रहा है. Currently eleventh slab complete हो चुका है out of twenty three. BMC से CC eighteenth floor तक मिल चुका है. New buyers के लिए thirteenth floor और उससे ऊपर के apartments available हैं. RERA possession twenty twenty nine है. अगर आप interested हैं तो एक बार site visit कर सकते हैं ताकि आप construction quality खुद देख सकें. कब convenient रहेगा?"

5. HANDLING COMMON QUESTIONS (Use exactly these answers):
   - Total floors? -> "G plus twenty two storey structure है. Already eleventh slab complete हो चुका है out of twenty three. Slab approximately one year में complete हो जाएगा."
   - Area/Size? -> "Two BHK के लिए six hundred four और six hundred nineteen square feet RERA carpet area के options available हैं. आपको कौन सा size suit करेगा?"
   - Price? -> [CRITICAL: Say EXACTLY this price. Do NOT change the numbers.] "अच्छा तो price बताती हूँ. छोटे two BHK की price है two point fifty seven crore all inclusive. और बड़े वाले की two point sixty seven crore all inclusive. All inclusive means agreement value plus stamp duty six percent plus GST five percent plus other charges सब included है. तो basically under three crore में South Bombay में two BHK मिल रहा है."
   - Payment plan? -> "अभी सिर्फ fifty lakh देना है own funds या bank financing से. उसके बाद possession तक कोई payment नहीं. आप stamp duty और GST pay करके property register भी करा सकते हैं जिससे risk और कम हो जाता है. क्या आप site visit करना चाहेंगे?"
   - Exact Location? -> "माहिम West में है, Jimmy Boy Bakery के opposite, Bank Of Baroda landmark, Desai Park. Railway station और Shitla Devi Metro Station walking distance पर हैं. Location बहुत बड़ा advantage है. मैं आपको location details share कर दूँगी."
   - Who is the developer? -> "Viyan Ventures ने develop किया है. Mr. Nayan Gandhi और Mr. Rohan Jain दोनो directors हैं. Western Mumbai production.. Goregaon Malad में काफ़ी experience है. ये उनका South Bombay का first project है माहिम West में."
   - Which floors available? -> "Thirteenth floor और ऊपर के apartments available हैं new buyers के लिए. Below thirteenth floor old society members को दिए गए हैं. ये एक redevelopment project है."
   - Construction quality? -> "Miven Technology से बन रहा है जो fast floor slab casting के लिए use होती है. Already eleventh slab complete है. BMC से CC eighteenth floor तक मिल चुका है. Nineteen to twenty two floor का CC process में है."

6. CLOSING & SCHEDULING:
   - INTERESTED -> ask date/time -> schedule_meeting
   - MAYBE -> offer project details -> schedule_callback
   - NOT INTERESTED -> ask concern -> address briefly -> end_call
--- END BUYER FLOW ---
"""

BROKER_INSTRUCTIONS = """
--- BROKER CALL FLOW (STRICT SCRIPT) ---
You are speaking with a BROKER (real estate channel partner).
Follow this exact sequence and wording to sound highly natural and expressive:

1. INITIAL GREETING:
   Say EXACTLY: "नमस्ते {caller_name} जी, कैसे हैं आप?"
   -> wait for user response.

2. INTRODUCTION:
   Say: "जी मैं र‍िया बोल रही हूँ Hookfish से. मैं आपको एक property opportunity के बारे में जानकारी देने के लिए call किया था. ये आपके clients के लिए काफी useful हो सकती है. क्या आपके पास एक minute है?"
   -> wait for user response.

3. FIRST PITCH:
   - If NO TARGET PROJECT is provided, pitch 'Maanikya': 
     "जी {caller_name} जी, हम माहिम West में एक नया project लेकर आए हैं - 'माणिक्य' by Viyan Ventures. ये South Bombay का prime location है. Railway station और Shitla Devi Metro Station दोनों walking distance पर हैं. यहाँ two BHK apartments हैं और इसका payment plan काफी attractive है—अभी सिर्फ fifty lakh देना है और possession तक कोई payment नहीं. क्या आप इसके बारे में और जानना चाहेंगे?"
   - If TARGET PROJECT is provided in DB Context below, pitch it naturally in a similar professional tone, highlighting location and payment plan.
   -> wait for user response.

4. SECOND PITCH / DETAILS (If they ask to know more):
   Say: "जी {caller_name} जी, मैं आपको details बता देती हूँ. ये एक redevelopment project है और construction Miven Technology से हो रहा है. Currently eleventh slab complete हो चुका है out of twenty three. BMC से CC eighteenth floor तक मिल चुका है. New buyers के लिए thirteenth floor और ऊपर के options हैं. Brokerage details भी काफी attractive हैं. अगर आप interested हैं तो एक बार site visit कर सकते हैं. Senior manager से मिलकर आप technical details discuss कर सकते हैं. कब convenient रहेगा?"

5. HANDLING COMMON QUESTIONS (Use exactly these answers):
   - Total floors? -> "G plus twenty two storey structure है. Already eleventh slab complete हो चुका है out of twenty three. Slab approximately one year में complete हो जाएगा."
   - Area/Size? -> "Two BHK के लिए six hundred four और six hundred nineteen square feet RERA carpet area के options available हैं."
   - Price? -> [CRITICAL: Say EXACTLY this price. Do NOT change the numbers.] "अच्छा तो price बताती हूँ. छोटे two BHK की price है two point fifty seven crore all inclusive. और बड़े वाले की two point sixty seven crore all inclusive. All inclusive means agreement value plus stamp duty six percent plus GST five percent plus other charges. Under three crore में South Bombay का two BHK—clients के लिए बहुत अच्छा deal है."
   - Payment plan? -> "अभी सिर्फ fifty lakh देना है. उसके बाद possession तक कोई payment नहीं. Buyer stamp duty और GST pay करके property register भी करा सकता है जिससे risk और कम हो जाता है."
   - Exact Location? -> "माहिम West में है, Jimmy Boy Bakery के opposite, Bank Of Baroda landmark, Desai Park. Railway station और Shitla Devi Metro Station walking distance पर हैं."
   - Who is the developer? -> "Viyan Ventures. Mr. Nayan Gandhi और Mr. Rohan Jain दोनो directors हैं. Western Mumbai Goregaon Malad में काफ़ी experience है. ये उनका South Bombay का first project है."
   - Which floors available? -> "Thirteenth floor और ऊपर available हैं new buyers के लिए. Below thirteenth floor old society members को दिए गए हैं. Redevelopment project है."
   - Construction quality? -> "Miven Technology से बन रहा है. Already eleventh slab complete है. BMC CC eighteenth floor तक मिल चुका है. Nineteen to twenty two floor का CC process में है."

6. BROKER QUALIFICATION & SCHEDULING:
   Ask: "आप currently किस area में काम कर रहे हैं?"
   - INTERESTED -> ask date/time -> schedule_meeting
   - MAYBE -> "कोई बात नहीं. मैं आपको complete project details share कर दूँगी. कब call back करूँ?" -> schedule_callback
   - NOT_INTERESTED -> "समझ गई. अगर बुरा ना मानें, specifically किस वजह से?" -> wait for answer -> end_call
--- END BROKER FLOW ---
"""

INBOUND_INSTRUCTIONS = """

--- INBOUND CALL INSTRUCTIONS ---
This is an INBOUND CALL -- meaning the person called you.
Start the call like this:
1. "Hello, Hookfish में आपका स्वागत है। मैं रिया बोल रही हूं। मैं आपकी कैसे मदद कर सकती हूं?"
2. Listen to their response and proceed accordingly
3. If they ask about a property, give details
4. If it's a complaint, listen, note it, and say "हमारी team 1 दिन के अंदर call back करेगी"
--- END INBOUND INSTRUCTIONS ---
"""


def build_agent_instructions(is_outbound: bool = False, phone_number: str = None,
                             contact_type: str = CONTACT_TYPE_BUYER,
                             caller_name_override: str = None,
                             target_project: str = None,
                             pre_fetched_data: dict = None) -> str:
    """Synchronously builds the instructions string using pre-fetched data."""
    caller_name = "Sir/Ma'am"
    db_context = ""

    if phone_number:
        # 1. Context from DB
        db_context = build_context_from_db(phone_number, pre_fetched_data=pre_fetched_data)
        
        # 2. Resolve Name (use pre-fetched if available)
        if pre_fetched_data:
            customer = pre_fetched_data.get("customer")
            if customer and customer.get("name"):
                caller_name = customer["name"].strip()
            else:
                leads = pre_fetched_data.get("leads", [])
                if leads and leads[0].get("partner_name"):
                    caller_name = leads[0]["partner_name"].strip()
        else:
            # Slow fallback
            customer = lookup_customer_by_phone(phone_number)
            if customer and customer.get("name"):
                caller_name = customer["name"].strip()

    # Override name
    if caller_name_override:
        caller_name = caller_name_override

    # Build instructions
    instructions = BASE_INSTRUCTIONS.replace("{caller_name}", caller_name)

    if not is_outbound:
        instructions += INBOUND_INSTRUCTIONS.replace("{caller_name}", caller_name)
    elif contact_type == CONTACT_TYPE_BROKER:
        instructions += BROKER_INSTRUCTIONS.replace("{caller_name}", caller_name)
    else:
        instructions += BUYER_INSTRUCTIONS.replace("{caller_name}", caller_name)

    # Add DB context
    if target_project:
        db_context += f"\n\n*** TARGET PROJECT ***\nProject Name: {target_project}\nSTRICT RULE: YOU MUST ONLY PITCH THIS TARGET PROJECT.\n"

    if db_context.strip():
        instructions += f"\n\n--- DATABASE CONTEXT ---\n{db_context.strip()}\n---\n"
    
    return instructions


# ============================================================
# Voice Agent Class
# ============================================================

class FilteredTTS(smallestai.TTS):
    """Wrapper to intercept and remove Cerebras LLaMA 3.1 function call leaks before TTS speaks it."""
    def synthesize(self, text: str, **kwargs):
        original_text = text
        
        # Strip everything starting from these backend keywords (case insensitive)
        pattern = r'(?i)(function name|capture outcome|parameters outcome|interest level|next action|end call parameters|reason call completed|schedule callback arguments).*'
        text = re.sub(pattern, '', text, flags=re.DOTALL).strip()
        
        if original_text != text:
            logger.warning(f"Intercepted LLM function call leak in TTS text. Original: '{original_text}' -> Filtered: '{text}'")

        if not text.strip():
            text = " "  # Avoid empty string error in TTS provider
            
        return super().synthesize(text, **kwargs)

class VoiceAgent(Agent):
    def __init__(self, instructions: str, is_outbound: bool = False, phone_number: str = None,
                 contact_type: str = CONTACT_TYPE_BUYER) -> None:

        logger.info(f"Agent initialized. Contact type: {contact_type}, Outbound: {is_outbound}")

        super().__init__(
            instructions=instructions,
            vad=silero.VAD.load(
                min_silence_duration=0.4,   # Wait 400ms of silence before cutting
                min_speech_duration=0.15,   # Need 150ms of speech to register as talking
            ),
            stt=deepgram.STT(
                model="nova-2",
                language="hi",
                interim_results=True,
                smart_format=False,
            ),
            # ULTRA-FAST SWITCH: Using Cerebras for sub-second responses.
            llm=openai.LLM(
                model="llama3.1-8b",
                api_key=os.environ.get("CEREBRAS_API_KEY"),
                base_url="https://api.cerebras.ai/v1"
            ),
            tts=smallestai.TTS(
                model="lightning-v3.1",
                voice_id="voice_BTq3OaiWFN",
                language="hi",
                sample_rate=16000,
                base_url="https://api.smallest.ai/waves/v1",
            ),
            min_endpointing_delay=0.25,    # Quick 250ms pause detection — fast but not choppy
            allow_interruptions=True,
        )
        self._is_outbound = is_outbound
        self._phone_number = phone_number
        self._contact_type = contact_type
        self._call_start_time = time.time()

    async def on_enter(self):
        if not self._is_outbound:
            await self.session.generate_reply()


# ============================================================
# SIP Identity Helper
# ============================================================

def extract_phone_from_identity(identity: str) -> str:
    """Extract phone number from SIP participant identity.
    Handles formats like: sip:+91xxx@domain, +91xxx, 91xxx, etc.
    """
    if not identity:
        return None

    # Try sip:+number@domain format
    sip_match = re.search(r'sip:(\+?\d+)@', identity)
    if sip_match:
        number = sip_match.group(1)
        if not number.startswith('+'):
            number = '+' + number
        return number

    # Try plain phone number (with or without +)
    phone_match = re.match(r'(\+?\d{10,15})$', identity)
    if phone_match:
        number = phone_match.group(1)
        if not number.startswith('+'):
            number = '+' + number
        return number

    return None


# ============================================================
# Entrypoint
# ============================================================

async def entrypoint(ctx: JobContext):
    logger.info(f"User connected to room: {ctx.room.name}")

    # ---- Parse metadata ----
    phone_number = None
    is_outbound = False
    contact_type = CONTACT_TYPE_BUYER  # default
    sip_trunk_id = os.getenv("TRUNK_ID", "")

    caller_name_override = None
    call_log_id = None  # Will track this call in agent_call_logs
    target_project = None

    if ctx.job.metadata:
        try:
            dial_info = json.loads(ctx.job.metadata)
            phone_number = dial_info.get("phone_number")
            contact_type = dial_info.get("contact_type", CONTACT_TYPE_BUYER)
            caller_name_override = dial_info.get("caller_name")  # optional name override
            target_project = dial_info.get("target_project")
            if phone_number:
                is_outbound = True
                logger.info(f"Outbound call detected. Target: {phone_number}, Type: {contact_type}, Name: {caller_name_override or 'from DB'}")
        except json.JSONDecodeError:
            pass

    # ---- Pre-call Optimized Data Fetch ----
    db_data = {"allowed": True, "reason": "OK"}
    if phone_number:
        logger.info(f"Pre-fetching optimized DB data for {phone_number}...")
        db_data = await asyncio.to_thread(get_agent_context_optimized, phone_number)
        
    # ---- Pre-call Validation ----
    if is_outbound and phone_number:
        if not db_data["allowed"]:
            logger.warning(f"Call NOT allowed to {phone_number}: {db_data['reason']}")
            ctx.shutdown(reason=f"call_blocked: {db_data['reason']}")
            return

        # Get name from pre-fetched data
        caller_name = caller_name_override
        if not caller_name:
            customer = db_data.get("customer")
            if customer:
                caller_name = customer.get("name")
            else:
                leads = db_data.get("leads", [])
                if leads:
                    caller_name = leads[0].get("partner_name")

        if not caller_name:
            caller_name = "Sir/Ma'am"

    # ---- Create Call Log ----
    if phone_number:
        call_log_id = create_call_log(
            call_id=ctx.room.name,
            phone_number=phone_number,
            caller_name=caller_name_override,
            contact_type=contact_type,
            direction="outbound" if is_outbound else "inbound",
            room_name=ctx.room.name,
        )
        record_call_attempt(phone_number, result="initiated", call_log_id=call_log_id)

    # ---- Instructions & Connect ----
    if is_outbound:
        await ctx.connect()
        logger.info("Building agent instructions (outbound)...")
        agent_instructions = await asyncio.to_thread(
            build_agent_instructions, True, phone_number, contact_type, caller_name_override, target_project, db_data
        )
    else:
        logger.info("Inbound call detected. Waiting for caller to connect...")
        await ctx.connect()

        # Check existing participants
        for participant in ctx.room.remote_participants.values():
            identity = participant.identity
            logger.info(f"Found existing participant: {identity} (name: {participant.name})")
            phone_number = extract_phone_from_identity(identity)
            if phone_number:
                logger.info(f"Extracted phone from existing participant: {phone_number}")
                break

        # If no participant yet, wait for one
        if not phone_number:
            logger.info("No participant yet, waiting for caller to join...")
            try:
                participant_event = asyncio.Event()
                found_phone = {"number": None}

                def on_participant_connected(participant: rtc.RemoteParticipant):
                    logger.info(f"Participant connected: {participant.identity} (name: {participant.name})")
                    extracted = extract_phone_from_identity(participant.identity)
                    if extracted:
                        found_phone["number"] = extracted
                        logger.info(f"Extracted phone from new participant: {extracted}")
                    participant_event.set()

                ctx.room.on("participant_connected", on_participant_connected)

                try:
                    await asyncio.wait_for(participant_event.wait(), timeout=30.0)
                    phone_number = found_phone["number"]
                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for participant. Proceeding without phone number.")
            except Exception as e:
                logger.error(f"Error waiting for participant: {e}")

        # Now fetch data for inbound
        if phone_number:
            logger.info(f"Fetching DB data for inbound caller {phone_number}...")
            db_data = await asyncio.to_thread(get_agent_context_optimized, phone_number)
            
            # Create call log for inbound
            if not call_log_id:
                call_log_id = create_call_log(
                    call_id=ctx.room.name,
                    phone_number=phone_number,
                    contact_type=contact_type,
                    direction="inbound",
                    room_name=ctx.room.name,
                )
                record_call_attempt(phone_number, result="initiated", call_log_id=call_log_id)

        logger.info("Building agent instructions (inbound)...")
        agent_instructions = await asyncio.to_thread(
            build_agent_instructions, False, phone_number, contact_type, caller_name_override, target_project, db_data
        )

    logger.info(f"Starting agent session. Phone: {phone_number or 'unknown'}, Type: {contact_type}, Outbound: {is_outbound}, Target: {target_project}")


    # ---- Transcript collector ----
    transcript_messages = []
    call_start_time = time.time()  # Track when the call actually starts
    MIN_CALL_BEFORE_OUTCOME = 30   # Minimum seconds before capture_outcome/end_call are allowed

    # ---- Define Function Tools ----

    @function_tool(
        name="end_call",
        description="Ends the current call. Use this AFTER capture_outcome, or when the user says goodbye."
    )
    async def end_call(reason: str | None = "call_completed") -> str:
        """End the call and disconnect.

        Args:
            reason: The reason for ending the call, e.g. 'user_requested', 'call_completed', 'max_duration', 'wrong_number', 'dnc_requested'
        """
        elapsed = time.time() - call_start_time
        if elapsed < MIN_CALL_BEFORE_OUTCOME:
            logger.warning(f"end_call BLOCKED — only {elapsed:.0f}s into call (min {MIN_CALL_BEFORE_OUTCOME}s). Greet the caller first!")
            return ""

        logger.info(f"end_call tool invoked. Reason: {reason}")

        # Save transcript and update call log
        if call_log_id:
            duration = int(time.time() - call_start_time)
            try:
                transcript_text = "\n".join(
                    [f"{m['role']}: {m['text']}" for m in transcript_messages]
                )
                update_call_log(
                    call_log_id,
                    duration_seconds=duration,
                    transcript=transcript_text,
                    notes=f"Call ended. Reason: {reason}",
                )
            except Exception as e:
                logger.error(f"Error saving final call data: {e}")

        async def _shutdown_after_delay():
            await asyncio.sleep(3)  # wait for goodbye TTS to finish
            try:
                job_ctx = get_job_context()
                await job_ctx.delete_room()
                job_ctx.shutdown(reason="end_call")
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")

        asyncio.create_task(_shutdown_after_delay())
        return "[SILENT] Done. Say goodbye naturally."

    @function_tool(
        name="capture_outcome",
        description=(
            "ONLY use this when the call is ENDING — NEVER at the start or middle of a call. "
            "You must have had a full conversation before using this. "
            "Use this to save the final call outcome and interest level. "
            "outcome values: 'interested', 'maybe', 'not_interested', 'callback_requested', 'escalated', 'dnc', 'wrong_number'. "
            "interest_level values: 'high', 'medium', 'low', 'none'."
        )
    )
    async def capture_outcome(
        outcome: str,
        reason: str | None = None,
        interest_level: str | None = "unknown",
        next_action: str | None = None,
    ) -> str:
        """Save the call outcome, interest level, and reason to the database.

        Args:
            outcome: The outcome of the call - 'interested', 'maybe', 'not_interested', 'callback_requested', 'escalated', 'dnc', 'wrong_number'
            reason: Why they are not interested or want to call back, free text
            interest_level: How interested they are - 'high', 'medium', 'low', 'none'
            next_action: What should happen next - 'callback', 'site_visit', 'send_details', 'escalate', 'none'
        """
        elapsed = time.time() - call_start_time
        if elapsed < MIN_CALL_BEFORE_OUTCOME:
            logger.warning(f"capture_outcome BLOCKED — only {elapsed:.0f}s into call (min {MIN_CALL_BEFORE_OUTCOME}s). Greet the caller first!")
            return ""
        logger.info(
            f"capture_outcome invoked. Phone: {phone_number}, "
            f"Outcome: {outcome}, Reason: {reason}, "
            f"Interest: {interest_level}, Next: {next_action}"
        )

        # Save to the existing leads system
        if phone_number:
            save_call_outcome(
                phone=phone_number,
                outcome=outcome,
                reason=reason or None,
                interest_level=interest_level,
                next_action=next_action or None,
            )

        # Also update the agent call log
        if call_log_id:
            # Map outcome to disposition
            disposition_map = {
                "interested": "qualified",
                "maybe": "follow_up",
                "not_interested": "rejected",
                "callback_requested": "callback",
                "escalated": "escalated",
                "dnc": "dnc",
                "wrong_number": "wrong_number",
            }
            update_call_log(
                call_log_id,
                disposition=disposition_map.get(outcome, outcome),
                qualification=outcome,
                interest_level=interest_level,
                outcome_reason=reason,
                next_action=next_action,
            )

        return "[SILENT] Done."

    @function_tool(
        name="schedule_callback",
        description=(
            "Use this to schedule a callback or follow-up. "
            "Use when the user asks to call back later."
        )
    )
    async def schedule_callback(
        callback_date: str | None = None,
        callback_time: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Schedule a callback or follow-up for later.

        Args:
            callback_date: Date for callback, e.g. 'kal', 'monday', '15 march'
            callback_time: Time for callback, e.g. 'shaam 5 baje', '3 PM'
            notes: Any additional notes about the callback
        """
        elapsed = time.time() - call_start_time
        if elapsed < MIN_CALL_BEFORE_OUTCOME:
            logger.warning(f"schedule_callback BLOCKED — only {elapsed:.0f}s into call. Greet first!")
            return ""

        callback_info = f"Date: {callback_date}, Time: {callback_time}"
        if notes:
            callback_info += f", Notes: {notes}"

        logger.info(f"schedule_callback invoked. Phone: {phone_number}, {callback_info}")

        if phone_number:
            save_call_outcome(
                phone=phone_number,
                outcome="callback_requested",
                next_action="callback",
                callback_date=f"{callback_date} {callback_time}".strip(),
            )

        # Update call log with callback info
        if call_log_id:
            update_call_log(
                call_log_id,
                next_action="callback",
                notes=f"Callback scheduled: {callback_info}",
            )

        return "[SILENT] Done."

    @function_tool(
        name="mark_as_dnc",
        description=(
            "Mark this phone number as Do Not Call. "
            "Use ONLY when the contact explicitly asks to be removed from the call list. "
            "Examples: 'mujhe call mat karo', 'remove me from your list', 'don't call again'."
        )
    )
    async def mark_as_dnc(reason: str | None = "user_requested") -> str:
        """Mark the current phone number as Do Not Call.

        Args:
            reason: Why they want to be removed, e.g. 'user_requested', 'not_interested_permanent'
        """
        elapsed = time.time() - call_start_time
        if elapsed < MIN_CALL_BEFORE_OUTCOME:
            logger.warning(f"mark_as_dnc BLOCKED — only {elapsed:.0f}s into call. Greet first!")
            return ""

        logger.info(f"mark_as_dnc invoked. Phone: {phone_number}, Reason: {reason}")

        if phone_number:
            db_mark_dnc(phone_number, reason=reason, added_by="agent")

        # Update call log
        if call_log_id:
            update_call_log(
                call_log_id,
                disposition="dnc",
                notes=f"DNC marked. Reason: {reason}",
            )

        return "[SILENT] Done."

    @function_tool(
        name="schedule_meeting",
        description=(
            "Schedule a site visit or meeting. Use when the buyer/broker agrees to a site visit or meeting. "
            "This will automatically allocate a manager using round-robin. "
            "Provide the date, time, and meeting type."
        )
    )
    async def schedule_meeting(
        meeting_date: str | None = None,
        meeting_time: str | None = None,
        meeting_type: str | None = "site_visit",
        project_name: str | None = None,
        location: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Schedule a meeting/site visit and auto-allocate a manager.

        Args:
            meeting_date: Date for the meeting, e.g. 'kal', 'monday', '15 march', '2026-03-20'
            meeting_time: Time for the meeting, e.g. 'subah 10 baje', '3 PM', '10:00'
            meeting_type: Type: 'site_visit', 'office_meeting', 'call_back'
            project_name: Name of the project/property for the meeting
            location: Meeting location if known
            notes: Any additional notes
        """
        elapsed = time.time() - call_start_time
        if elapsed < MIN_CALL_BEFORE_OUTCOME:
            logger.warning(f"schedule_meeting BLOCKED — only {elapsed:.0f}s into call. Greet first!")
            return ""

        logger.info(f"schedule_meeting invoked. Phone: {phone_number}, Date: {meeting_date}, Time: {meeting_time}")

        try:
            start_dt, _ = parse_meeting_datetime(meeting_date, meeting_time)
            clean_date = start_dt.strftime("%Y-%m-%d")
            clean_time = start_dt.strftime("%H:%M:%S")
        except Exception as e:
            logger.warning(f"Failed to parse datetime for DB: {e}")
            clean_date, clean_time = meeting_date, meeting_time

        def _bg_task():
            # Auto-allocate a manager (round-robin)
            manager = allocate_manager(project_name=project_name or None)
            manager_name = manager["name"] if manager else None
            manager_id = manager["id"] if manager else None

            # Get caller name
            caller_name = caller_name_override
            if not caller_name and phone_number:
                customer = lookup_customer_by_phone(phone_number)
                if customer:
                    caller_name = customer.get("name")

            # Create meeting record
            meeting_id = None
            if phone_number:
                try:
                    meeting_id = create_meeting(
                        phone_number=phone_number,
                        contact_name=caller_name,
                        contact_type=contact_type,
                        meeting_type=meeting_type,
                        meeting_date=clean_date,
                        meeting_time=clean_time,
                        location=location,
                        project_name=project_name,
                        manager_id=manager_id,
                        manager_name=manager_name,
                        call_log_id=call_log_id,
                        notes=notes,
                    )
                except Exception as db_err:
                    logger.error(f"Error creating meeting in DB: {db_err}")

            # Update call log
            if call_log_id:
                try:
                    update_call_log(
                        call_log_id,
                        next_action="site_visit" if meeting_type == "site_visit" else "meeting",
                        manager_assigned=manager_name,
                        notes=f"Meeting ({meeting_type}) scheduled: {clean_date} {clean_time}. Manager: {manager_name or 'TBD'}",
                    )
                except Exception as log_err:
                    logger.error(f"Error updating call log: {log_err}")

            # ---- Google Calendar Integration ----
            try:
                manager_email = get_manager_email(manager_id) if manager_id else None

                cal_result = schedule_meeting_on_calendar(
                    contact_name=caller_name or "Contact",
                    contact_phone=phone_number or "",
                    contact_type=contact_type,
                    meeting_type=meeting_type,
                    date_str=meeting_date,
                    time_str=meeting_time,
                    project_name=project_name,
                    location=location,
                    manager_name=manager_name or "",
                    manager_email=manager_email or "",
                    notes=notes,
                )

                if cal_result["success"] and cal_result["event_id"]:
                    if meeting_id:
                        update_meeting_calendar(
                            meeting_id,
                            calendar_event_id=cal_result["event_id"],
                            calendar_invite_sent=True,
                        )
                    logger.info(f"Calendar event created: {cal_result['event_id']}")
                elif not cal_result["available"]:
                    logger.warning(f"Calendar slot unavailable: {cal_result['message']}")
                else:
                    logger.warning(f"Calendar event creation failed: {cal_result['message']}")
            except Exception as e:
                logger.error(f"Error creating calendar event: {e}")

        # Run heavily blocking DB/HTTP logic in background thread!
        asyncio.create_task(asyncio.to_thread(_bg_task))
        return "[SILENT] Done."
        #
        #         wa_result = send_and_log_meeting_details(
        #             to_phone=phone_number,
        #             contact_name=caller_name or "Customer",
        #             meeting_type=meeting_type,
        #             meeting_date=meeting_date,
        #             meeting_time=meeting_time,
        #             project_name=project_name,
        #             location=location,
        #             manager_name=manager_name or "",
        #             manager_phone=manager_phone_num,
        #             notes=notes,
        #             meeting_id=meeting_id,
        #             call_log_id=call_log_id,
        #         )
        #
        #         if wa_result.get("success"):
        #             whatsapp_msg = " Meeting details have been sent to WhatsApp."
        #             logger.info(f"WhatsApp meeting confirmation sent to {phone_number}")
        #         else:
        #             whatsapp_msg = " WhatsApp message could not be sent, but meeting is saved."
        #             logger.warning(f"WhatsApp send failed for {phone_number}: {wa_result.get('message')}")
        # except Exception as e:
        #     logger.error(f"Error sending WhatsApp meeting confirmation: {e}")
        #     whatsapp_msg = " Meeting saved (WhatsApp notification pending)."

        # Return minimal silent response — agent must NOT read this aloud
        return "[SILENT] Done."



    # ---- Create Agent Session ----
    session = AgentSession(
        allow_interruptions=True,
        min_interruption_duration=0.5,   # Need 500ms of speech to interrupt (avoids noise/cough triggers)
        min_interruption_words=1,       # Must say at least 1 word to interrupt (not just sounds)
        min_endpointing_delay=0.25,     # Quick response after user stops
        max_endpointing_delay=0.8,      # Max 800ms wait for mid-sentence pauses
        preemptive_generation=True,     # Re-enabled: Sweden Central is faster, preemptive helps!
        aec_warmup_duration=0,          # Disable 3s initialization delay
        tools=[schedule_callback, mark_as_dnc, schedule_meeting], # temporarily removed end_call, capture_outcome
    )

    # ---- Track transcript ----
    agent_start_time = time.time()

    @session.on("agent_speech_committed")
    def on_agent_speech(msg):
        transcript_messages.append({"role": "agent", "text": str(msg)})

    @session.on("user_speech_committed")
    def on_user_speech(msg):
        transcript_messages.append({"role": "user", "text": str(msg)})

    # ---- Start Session ----
    voice_agent = VoiceAgent(
        instructions=agent_instructions,
        is_outbound=is_outbound,
        phone_number=phone_number,
        contact_type=contact_type,
    )
    
    await session.start(
        agent=voice_agent,
        room=ctx.room,
    )

    # ---- 4-Minute Auto-End Timer ----
    async def _auto_end_timer():
        """Hard cutoff: auto-end the call after MAX_CALL_DURATION."""
        await asyncio.sleep(MAX_CALL_DURATION)
        logger.info(f"Call limit of {MAX_CALL_DURATION}s reached. Force ending call.")

        # Save final call data
        if call_log_id:
            try:
                transcript_text = "\n".join(
                    [f"{m['role']}: {m['text']}" for m in transcript_messages]
                )
                update_call_log(
                    call_log_id,
                    disposition="max_duration",
                    duration_seconds=MAX_CALL_DURATION,
                    transcript=transcript_text,
                    notes="Call auto-ended due to max duration",
                )
            except Exception as e:
                logger.error(f"Error saving call data on auto-end: {e}")

        try:
            job_ctx = get_job_context()
            await job_ctx.delete_room()
            job_ctx.shutdown(reason="max_duration_reached")
        except Exception as e:
            logger.error(f"Error during auto-end: {e}")

    asyncio.create_task(_auto_end_timer())

    # ---- Outbound: Place the SIP call ----
    if is_outbound and phone_number:
        logger.info(f"Placing outbound call to: {phone_number}")
        lkapi = api.LiveKitAPI(
            url=os.getenv("LIVEKIT_URL"),
            api_key=os.getenv("LIVEKIT_API_KEY"),
            api_secret=os.getenv("LIVEKIT_API_SECRET"),
        )
        try:
            await lkapi.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=sip_trunk_id,
                    sip_call_to=phone_number,
                    participant_identity=phone_number,
                    wait_until_answered=True,
                )
            )
            logger.info("Call picked up successfully! Triggering agent greeting.")
            if phone_number:
                record_call_attempt(phone_number, result="answered", call_log_id=call_log_id)
                
            # Wait a brief moment for the audio track to stabilize, then greet!
            async def trigger_greeting():
                logger.info("Agent playing initial outbound greeting directly via TTS...")
                c_name = caller_name if 'caller_name' in locals() and caller_name else "सर/मैडम"
                try:
                    # Warm, casual greeting — like calling a friend
                    await voice_agent.session.say(f"हेलो, {c_name} जी!", add_to_chat_ctx=True)
                    await voice_agent.session.say("कैसे हैं आप? सब बढ़िया?", add_to_chat_ctx=True)
                except AttributeError:
                    # Fallback if say doesn't exist or isn't async
                    import livekit.agents.llm as llm_model
                    voice_agent.session.chat_ctx.messages.append(llm_model.ChatMessage(role="assistant", content=f"नमस्ते {c_name} जी, कैसे हैं आप?"))
                    await voice_agent.session.generate_reply()
            asyncio.create_task(trigger_greeting())
            
        except Exception as e:
            logger.error(f"Error placing outbound call: {e}")
            # Record failed attempt
            if phone_number:
                record_call_attempt(phone_number, result="failed", call_log_id=call_log_id)
            if call_log_id:
                update_call_log(call_log_id, disposition="failed", notes=f"Call failed: {e}")
            ctx.shutdown()
        finally:
            await lkapi.aclose()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="hookfish-voice-agent",
        )
    )
