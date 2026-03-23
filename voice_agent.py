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
from livekit.plugins import groq, deepgram, sarvam, elevenlabs
from livekit import api, rtc

from db_helper import (
    lookup_customer_by_phone,
    lookup_lead_by_phone,
    lookup_property_by_name,
    save_call_conversation,
    update_lead_status,
    save_call_outcome,
    get_project_details_for_lead,
    # New imports for agent flow
    is_dnc,
    mark_dnc as db_mark_dnc,
    create_call_log,
    update_call_log,
    save_call_transcript,
    check_call_allowed,
    record_call_attempt,
    allocate_manager,
    create_meeting,
    get_meetings_for_phone,
    update_meeting_calendar,
    get_manager_email,
)

from google_calendar import schedule_meeting_on_calendar

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-agent")

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
def build_context_from_db(phone_number: str) -> str:
    """
    Query the database for all info related to a phone number
    and build a context string the agent can use in conversation.
    Now includes project/property details and meeting history.
    """
    context_parts = []

    # 1. Look up customer
    customer = lookup_customer_by_phone(phone_number)
    if customer:
        context_parts.append(
            f"Customer Info: Name = {customer['name']}, "
            f"ID = {customer['id']}, Source = {customer.get('origin', 'N/A')}"
        )

    # 2. Look up leads associated with this phone
    leads = lookup_lead_by_phone(phone_number)
    if leads:
        context_parts.append(f"\nFound {len(leads)} leads for this number:")
        for lead in leads:
            lead_info = (
                f"  - Lead #{lead['id']}: Customer = {lead['customer_name']}, "
                f"Property = {lead['property_name']}, Status = {lead['status']}"
            )
            if lead.get('notes'):
                lead_info += f", Notes = {lead['notes']}"
            if lead.get('followup'):
                lead_info += f", Follow-up = {lead['followup']}"
            context_parts.append(lead_info)

    # 3. Look up project/property details
    projects = get_project_details_for_lead(phone_number)
    if projects:
        context_parts.append(f"\nRelated Project/Property Details:")
        for proj in projects:
            proj_info = f"  - {proj['name']}"
            if proj.get('type'):
                proj_info += f" (Type: {proj['type']})"
            if proj.get('alias'):
                proj_info += f" | Alias: {proj['alias']}"
            if proj.get('commission_percentage'):
                proj_info += f" | Commission: {proj['commission_percentage']}%"
            if proj.get('site_visit_bonus'):
                proj_info += f" | Site Visit Bonus: {proj['site_visit_bonus']}"
            if proj.get('guarantee_for_sale'):
                proj_info += f" | Guarantee: {proj['guarantee_for_sale']}"
            context_parts.append(proj_info)

    # 4. Check for past meetings
    meetings = get_meetings_for_phone(phone_number)
    if meetings:
        context_parts.append(f"\nPast Meetings/Appointments:")
        for mtg in meetings:
            mtg_info = f"  - {mtg['meeting_type']}: {mtg.get('meeting_date', 'TBD')}"
            if mtg.get('project_name'):
                mtg_info += f" | Project: {mtg['project_name']}"
            if mtg.get('manager_name'):
                mtg_info += f" | Manager: {mtg['manager_name']}"
            mtg_info += f" | Status: {mtg['status']}"
            context_parts.append(mtg_info)

    if not context_parts:
        context_parts.append(
            f"No previous information found for this number ({phone_number}). This may be a new contact."
        )

    return "\n".join(context_parts)


# ============================================================
# Agent Instructions
# ============================================================

BASE_INSTRUCTIONS = """\
You are calling on behalf of Hookfish. Your name is 'Riya'. You are a female calling assistant who speaks with real estate brokers and customers.

Identity and Tone:
- Your language will be very Natural, Polite and Conversational Hindi-English mix.
- Your voice should have a smile and composure.
- You won't talk like a robot, but like a normal person.
- Use conversational fillers like "ji", "achha", "haan bilkul" at the right places.
- Use feminine Hindi grammar -- "main bata sakti hoon", "main samajhti hoon", "maine bhej di hai".
- Always speak in first person -- "hum", "humari company", "humari team". Don't refer to Hookfish in third person.

During the call:
- If the Customer's voice is not heard in between or there is silence: "{caller_name} ji, kya aap mujhe sun sakte hain?"
- Keep your responses short (1-2 sentences at a time), except when delivering a specific pitch.
- Only proceed after the Customer responds. Don't give long speeches.
- Ask for permission before moving forward.

Objection handling (priority: objection > trust > clarification > continue):
- "Brokers ko replace kar rahe ho?" --> "Nahi, hum brokers ko empower karte hain. Brokers ke bina real estate possible nahi hai. Hum aapki madad ke liye hain."
- "Buyers ko directly call nahi kar sakte?" --> "Hum aapko buyer interest data dete hain taaki aapke follow-ups zyada effective hon."
- "Mera kaam limit hoga?" --> "Bilkul nahi. Behtar information se aapki baatcheet aur strong hogi."
- "Aise platforms aate-jaate rehte hain" --> "Aapka experience valid hai. Hum practical value dene par focus karte hain, bade-bade claims par nahi."

DNC (DO NOT CALL) HANDLING (Section 5.1 - CRITICAL):
- If the contact says "remove me from your list", "mujhe call mat karo", "don't call me again", "mera number hata do":
  1. Say: "Ji bilkul, main aapka number humari list se hata deti hoon. Aapko aage se koi call nahi aayegi. Dhanyavaad."
  2. Use the mark_as_dnc tool immediately.
  3. Then use end_call tool.

WRONG NUMBER HANDLING (Section 8.1):
- If the contact says this is a wrong number, or they are not the intended person:
  1. Say: "Maafi chahti hoon, galat number dial ho gaya lagta hai. Aapko disturb karne ke liye sorry. Dhanyavaad."
  2. Use end_call tool immediately.
- If the contact name does not match: apologize and end the call.

ABUSIVE / AGGRESSIVE CALLER (Section 8.1):
- If the caller is abusive or aggressive:
  1. Stay calm and say: "Main samajhti hoon. Maafi chahti hoon agar aapko koi takleef hui. Kya hum kisi aur samay baat kar sakte hain?"
  2. If they continue being abusive, say: "Dhanyavaad aapke time ke liye. Hum aapko baad mein call karenge." and use end_call.

BUSY / DRIVING / MINOR (Section 8.1):
- If the caller says "main drive kar raha hoon", "baad mein call karo", "abhi busy hoon":
  1. Say: "Koi baat nahi! Aapko kab call karna sahi rahega?"
  2. If they give a time, use schedule_callback tool.
  3. If they don't, say: "Main kal dubara try karti hoon. Dhanyavaad!" and end the call.

DIFFERENT LANGUAGE (Section 8.1):
- If the caller speaks in a different language or says they don't understand Hindi:
  1. Try responding in English if possible.
  2. If still unable to communicate: "I understand. Let me connect you with someone who speaks your language. Thank you!"

REPEAT LIMIT (Section 8.2):
- If you don't understand the caller, you may say "Sorry, kya aap dubara bol sakte hain?" at most 3 times.
- After 3 failed attempts: "Maafi chahti hoon, mujhe sahi se sun nahi aa raha. Humari team aapko call back karegi. Dhanyavaad."
  Then use end_call.

TECHNICAL ISSUES (Section 8.2):
- If the call drops or you sense connection issues:
  Agent should note this - the system will handle re-calling.
- If database/CRM is unavailable: continue the call naturally, the data will be saved locally.

Limits:
- Never force a sale, guess, or invent features
- Don't share company's sensitive information without authorization
- Don't mention AI, automation, or internal workflows
- Keep answers precise, avoid fillers or repetition
- If unsure, ask clarifying questions -- don't guess
- If you've spoken before, naturally acknowledge and continue from there
- CRITICAL TEXT RULES: Speak perfectly plain text. NO MARKDOWN, asterisks (*), underscores (_), bolding, or special characters.
- DO NOT use the % symbol (say "percent"), and DO NOT use commas in numbers (say "5000", not "5,000" or just spell them out).
- DO NOT ever use foreign scripts like Chinese characters. ONLY use Hindi Devanagari script and English letters.

OUT-OF-CONTEXT QUESTIONS:
If a question comes that is outside your expertise or agenda, say:
"Yeh ek bahut achha sawal hai. Iske liye main aapki baat humare senior manager se schedule kara doongi, wo aapko behtar guide kar payenge."

ESCALATION (Complex question or Complaint):
If there's a complex question or complaint you cannot handle:
"Main samajhti hoon. Iske liye humari team aapko 1 din ke andar call back karegi aur properly assist karegi."
(DO NOT end the call yet. Wait for the user to say okay or bye).

CALL DURATION (Very Important):
The MAXIMUM duration for this call is 4 minutes. If the conversation is getting too long, naturally wrap it up.

TOOL USAGE RULES (CRITICAL):
NEVER use the capture_outcome, mark_as_dnc, or end_call tools at the start of the conversation. 
Wait until the user clearly expresses interest, refuses to talk, or the conversation naturally concludes before using these tools. If the user just says "Hello" or "ji bataiye", DO NOT end the call -- continue with your pitch!

CALL ENDING (Required Steps -- Follow on EVERY call):
When the call is ending (either requested by the user, or max duration reached):
1. First, use the capture_outcome tool to save the outcome, interest_level, reason, and next_action.
2. Then, provide a SHORT SUMMARY of the next steps (e.g., "toh main aapko details bhej doongi aur kal shaam call back karoongi").
3. "Bahut shukriya aapke time ke liye {caller_name} ji. Achha din ho!"
4. Finally, use the end_call tool.

CALL TERMINATION TRIGGERS:
- ONLY end the call if the user explicitly says phrases like: "rakhta hu", "call cut krta hu", "end", "bye", "alvida", "hang up", "call later", "phone rakh raha hu".
- IMPORTANT: If the user explicitly says casual agreement words like "haan haan bhai", "haan", "achha", "ji", DO NOT end the call! Just acknowledge and continue your pitch naturally!

Your goal: Build trust and understanding, not force outcomes.
"""


BUYER_INSTRUCTIONS = """

--- BUYER CALL FLOW ---
You are speaking with a BUYER (potential customer).

Start the call EXACTLY like this (Strict Flow -- follow each step):
1. Wait for them to say hello, then say: "{caller_name} ji main Riya bol rahi hoon. Actually aapko ek property opportunity ke baare mein call kiya tha. Ek minute hai aapke paas?"
2. Wait for their response (e.g., "bolo", "haan").

PROJECT PITCH (Part 1 - Location & Payment Plan):
3. After they agree to listen, say EXACTLY:
"Ji {caller_name} ji, actually hum Mahim West mein ek naya project leke aaye hain, Manikya. South Bombay ka prime location hai. Station se sirf 5-6 minute door. 2 BHK apartments hain. Aur sabse best part: payment plan. Abhi sirf 50 lakh dena hai, uske baad possession tak koi payment nahi. Matlab no EMI burden for almost 2 years! Aur bataoon iske baare mein?"

PROJECT PITCH (Part 2 - Construction & Call to Action):
4. Wait for them to say "haan" or show interest, then say EXACTLY:
"Great {caller_name} ji. Toh main aapko bata deti hoon construction already 9th floor tak complete ho gaya hai. Mivan technology se ban raha hai, quality bahut strong hai. Possession next year June-September tak expected hai. Abhi early bird discounts chal rahe hain toh kuch additional benefits bhi mil sakte hain. Agar aap interested hain toh ek baar site visit kar sakte hain. Main details WhatsApp kar doon ya direct site visit schedule kar lein?"

Understand INTEREST LEVEL from their response to the site visit question:
- INTERESTED (wants site visit):
  --> "Bahut achha! Kaun sa din aur time sahi rahega visit ke liye?"
  --> Once they give a date/time, use schedule_meeting tool.

- MAYBE (wants details on WhatsApp):
  --> "Sure, main aapko project ki brochure aur details WhatsApp par bhej deti hoon. Aap aaram se dekh lijiye. Kab call back karoon aapko?"
  --> Use schedule_callback tool if they give a time.

- NOT_INTERESTED:
  --> "{caller_name} ji, koi baat nahi. Agar bura na maanein toh bata sakte hain specifically kya concern hai? Budget ya location?"
  --> Address briefly, then say goodbye and use end_call.

--- END BUYER FLOW ---
"""


BROKER_INSTRUCTIONS = """

--- BROKER CALL FLOW ---
You are speaking with a BROKER (real estate broker / channel partner).

Start the call like this (Strict Flow):
1. User: "Hello"
2. AI: "Namaste, main Hookfish se Riya bol rahi hoon. Humare paas ek nayi property listing aayi hai jo aapke clients ke liye kaafi achhi ho sakti hai. Kya aap 2 minute baat kar sakte hain?"

(DO NOT use any tools here. Just speak.)

PROJECT AWARENESS:
- Tell project name, location, type
- Share commission structure (if available)
- Share site visit bonus (if applicable)
- 1-2 key selling points

QUALIFICATION QUESTIONS (Broker-specific, ask naturally):
1. "Aap currently kis area mein kaam kar rahe hain?"
2. "Is project/area mein aapka interest hai?"
3. "Aapke clients ka generally price range kya hai?"
4. "Kya aap site visit schedule karna chahenge?"

Broker Response Handling:
- INTERESTED:
  --> "Bahut achha! Kya hum ek site visit schedule kar lein? Kaun sa din aur time sahi rahega?"
  --> If they give a date/time, use schedule_meeting tool to book it.

- MAYBE:
  --> "Koi baat nahi. Main aapko complete project details bhej doongi. Kab call back karoon?"
  --> Use the schedule_callback tool only if they ask for a callback.

- NOT_INTERESTED:
  --> "Samajh gayi. Agar bura na maanein, specifically kis wajah se?"
  --> Wait for their answer. When they finish, say goodbye and end the call.

--- END BROKER FLOW ---
"""


INBOUND_INSTRUCTIONS = """

--- INBOUND CALL INSTRUCTIONS ---
This is an INBOUND CALL -- meaning the person called you.
Start the call like this:
1. "Hello, Hookfish mein aapka swagat hai. Main Riya bol rahi hoon. Main aapki kaise madad kar sakti hoon?"
2. Listen to their response and proceed accordingly
3. If they ask about a property, give details
4. If it's a complaint, listen, note it, and say "humari team 1 din ke andar call back karegi"
--- END INBOUND INSTRUCTIONS ---
"""


# ============================================================
# Voice Agent Class
# ============================================================

class VoiceAgent(Agent):
    def __init__(self, is_outbound: bool = False, phone_number: str = None,
                 contact_type: str = CONTACT_TYPE_BUYER,
                 caller_name_override: str = None) -> None:

        # Build dynamic instructions with DB context
        caller_name = "Sir/Ma'am"
        db_context = ""

        if phone_number:
            logger.info(f"Looking up database for phone: {phone_number}")
            db_context = build_context_from_db(phone_number)
            logger.info(f"DB context loaded:\n{db_context}")

            # Try to extract name from customer or lead data
            customer = lookup_customer_by_phone(phone_number)
            if customer and customer.get("name"):
                caller_name = customer["name"].strip()
            else:
                leads = lookup_lead_by_phone(phone_number)
                if leads and leads[0].get("partner_name"):
                    caller_name = leads[0]["partner_name"].strip()

        # Override with explicit name if provided
        if caller_name_override:
            caller_name = caller_name_override
            logger.info(f"Using caller name override: {caller_name}")

        # Build instructions based on contact type
        instructions = BASE_INSTRUCTIONS.replace("{caller_name}", caller_name)

        # Add contact-type specific flow
        if not is_outbound:
            instructions += INBOUND_INSTRUCTIONS.replace("{caller_name}", caller_name)
        elif contact_type == CONTACT_TYPE_BROKER:
            instructions += BROKER_INSTRUCTIONS.replace("{caller_name}", caller_name)
        else:
            instructions += BUYER_INSTRUCTIONS.replace("{caller_name}", caller_name)

        # Add DB context
        if db_context:
            instructions += f"""

--- DATABASE CONTEXT (for this call) ---
{db_context}
---
Use the above information to personalize the conversation.
If property/project details are available, use them in the PROJECT AWARENESS section.
If there have been prior leads or interactions, naturally reference them.
But don't share all information at once -- share as needed.
"""

        logger.info(f"Agent initialized. Contact type: {contact_type}, Outbound: {is_outbound}")

        super().__init__(
            instructions=instructions,
            stt=deepgram.STT(model="nova-2", language="hi"),
            llm=groq.LLM(model="meta-llama/llama-4-scout-17b-16e-instruct"),
            tts=elevenlabs.TTS(
                voice_id="cgSgspJ2msm6clMCkdW9",  # Jessica - default, conversational female
                model="eleven_multilingual_v2",
                language="hi",
            ),
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

    if ctx.job.metadata:
        try:
            dial_info = json.loads(ctx.job.metadata)
            phone_number = dial_info.get("phone_number")
            contact_type = dial_info.get("contact_type", CONTACT_TYPE_BUYER)
            caller_name_override = dial_info.get("caller_name")  # optional name override
            if phone_number:
                is_outbound = True
                logger.info(f"Outbound call detected. Target: {phone_number}, Type: {contact_type}, Name: {caller_name_override or 'from DB'}")
        except json.JSONDecodeError:
            pass

    # ---- Pre-call Validation (Section 8.3 + 6.3) ----
    if is_outbound and phone_number:
        # Check if call is allowed (DNC + max 1/day)
        call_check = check_call_allowed(phone_number)
        if not call_check["allowed"]:
            logger.warning(f"Call NOT allowed to {phone_number}: {call_check['reason']}")
            ctx.shutdown(reason=f"call_blocked: {call_check['reason']}")
            return

        # Validate that we have a name (Section 8.3: no call without name)
        caller_name = caller_name_override
        if not caller_name:
            customer = lookup_customer_by_phone(phone_number)
            if customer and customer.get("name"):
                caller_name = customer["name"].strip()
            else:
                leads = lookup_lead_by_phone(phone_number)
                if leads and leads[0].get("partner_name"):
                    caller_name = leads[0]["partner_name"].strip()

        if not caller_name or caller_name == "Sir/Ma'am":
            logger.warning(f"Call NOT allowed to {phone_number}: No contact name found")
            ctx.shutdown(reason="call_blocked: no_contact_name")
            return

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
        # Record the call attempt
        record_call_attempt(phone_number, result="initiated", call_log_id=call_log_id)

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
            logger.info("Call picked up successfully")
            # Update attempt result
            if phone_number:
                record_call_attempt(phone_number, result="answered", call_log_id=call_log_id)
        except Exception as e:
            logger.error(f"Error placing outbound call: {e}")
            # Record failed attempt
            if phone_number:
                record_call_attempt(phone_number, result="failed", call_log_id=call_log_id)
            if call_log_id:
                update_call_log(call_log_id, disposition="failed", notes=f"Call failed: {e}")
            ctx.shutdown()
            return
        finally:
            await lkapi.aclose()

    # ---- Inbound: Wait for SIP participant ----
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

        # Create call log for inbound (if we got a phone number)
        if phone_number and not call_log_id:
            call_log_id = create_call_log(
                call_id=ctx.room.name,
                phone_number=phone_number,
                contact_type=contact_type,
                direction="inbound",
                room_name=ctx.room.name,
            )

    logger.info(f"Starting agent session. Phone: {phone_number or 'unknown'}, Type: {contact_type}, Outbound: {is_outbound}")

    # ---- Transcript collector ----
    transcript_messages = []

    # ---- Define Function Tools ----

    @function_tool(
        name="end_call",
        description="Ends the current call. Use this AFTER capture_outcome, or when the user says goodbye."
    )
    async def end_call(reason: str = "call_completed") -> str:
        """End the call and disconnect.

        Args:
            reason: The reason for ending the call, e.g. 'user_requested', 'call_completed', 'max_duration', 'wrong_number', 'dnc_requested'
        """
        logger.info(f"end_call tool invoked. Reason: {reason}")

        # Save transcript and update call log
        if call_log_id:
            duration = int(time.time() - time.time())  # Will be updated properly
            try:
                transcript_text = "\n".join(
                    [f"{m['role']}: {m['text']}" for m in transcript_messages]
                )
                update_call_log(
                    call_log_id,
                    duration_seconds=int(time.time() - agent_start_time) if 'agent_start_time' in dir() else 0,
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
        return "Say goodbye politely and provide a summary of the next steps. The call is ending."

    @function_tool(
        name="capture_outcome",
        description=(
            "Use this to save the call outcome and interest level. "
            "You MUST use this at the end of EVERY call. "
            "outcome values: 'interested', 'maybe', 'not_interested', 'callback_requested', 'escalated', 'dnc', 'wrong_number'. "
            "interest_level values: 'high', 'medium', 'low', 'none'."
        )
    )
    async def capture_outcome(
        outcome: str,
        reason: str = "",
        interest_level: str = "unknown",
        next_action: str = "",
    ) -> str:
        """Save the call outcome, interest level, and reason to the database.

        Args:
            outcome: The outcome of the call - 'interested', 'maybe', 'not_interested', 'callback_requested', 'escalated', 'dnc', 'wrong_number'
            reason: Why they are not interested or want to call back, free text
            interest_level: How interested they are - 'high', 'medium', 'low', 'none'
            next_action: What should happen next - 'callback', 'site_visit', 'send_details', 'escalate', 'none'
        """
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

        return f"Outcome saved: {outcome}. Now end the call naturally."

    @function_tool(
        name="schedule_callback",
        description=(
            "Use this to schedule a callback or follow-up. "
            "Use when the user asks to call back later."
        )
    )
    async def schedule_callback(
        callback_date: str = "",
        callback_time: str = "",
        notes: str = "",
    ) -> str:
        """Schedule a callback or follow-up for later.

        Args:
            callback_date: Date for callback, e.g. 'kal', 'monday', '15 march'
            callback_time: Time for callback, e.g. 'shaam 5 baje', '3 PM'
            notes: Any additional notes about the callback
        """
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

        return f"Callback scheduled: {callback_info}. Confirm this with the user."

    @function_tool(
        name="mark_as_dnc",
        description=(
            "Mark this phone number as Do Not Call. "
            "Use ONLY when the contact explicitly asks to be removed from the call list. "
            "Examples: 'mujhe call mat karo', 'remove me from your list', 'don't call again'."
        )
    )
    async def mark_as_dnc(reason: str = "user_requested") -> str:
        """Mark the current phone number as Do Not Call.

        Args:
            reason: Why they want to be removed, e.g. 'user_requested', 'not_interested_permanent'
        """
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

        return "Number marked as Do Not Call. Now apologize politely and end the call."

    @function_tool(
        name="schedule_meeting",
        description=(
            "Schedule a site visit or meeting. Use when the buyer/broker agrees to a site visit or meeting. "
            "This will automatically allocate a manager using round-robin. "
            "Provide the date, time, and meeting type."
        )
    )
    async def schedule_meeting(
        meeting_date: str = "",
        meeting_time: str = "",
        meeting_type: str = "site_visit",
        project_name: str = "",
        location: str = "",
        notes: str = "",
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
        logger.info(f"schedule_meeting invoked. Phone: {phone_number}, Date: {meeting_date}, Time: {meeting_time}")

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
            meeting_id = create_meeting(
                phone_number=phone_number,
                contact_name=caller_name,
                contact_type=contact_type,
                meeting_type=meeting_type,
                meeting_date=meeting_date,
                meeting_time=meeting_time,
                location=location,
                project_name=project_name,
                manager_id=manager_id,
                manager_name=manager_name,
                call_log_id=call_log_id,
                notes=notes,
            )

        # Update call log
        if call_log_id:
            update_call_log(
                call_log_id,
                next_action="site_visit" if meeting_type == "site_visit" else "meeting",
                manager_assigned=manager_name,
                notes=f"Meeting ({meeting_type}) scheduled: {meeting_date} {meeting_time}. Manager: {manager_name or 'TBD'}",
            )

        # ---- Google Calendar Integration ----
        calendar_msg = ""
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
                # Save calendar event ID to the meeting record
                if meeting_id:
                    update_meeting_calendar(
                        meeting_id,
                        calendar_event_id=cal_result["event_id"],
                        calendar_invite_sent=True,
                    )
                calendar_msg = f" Calendar invite has been created for {cal_result['start']}."
                logger.info(f"Calendar event created: {cal_result['event_id']}")
            elif not cal_result["available"]:
                calendar_msg = f" Note: {cal_result['message']}"
                logger.warning(f"Calendar slot unavailable: {cal_result['message']}")
            else:
                calendar_msg = " Calendar invite could not be sent, but meeting is saved."
                logger.warning(f"Calendar event creation failed: {cal_result['message']}")
        except Exception as e:
            logger.error(f"Error creating calendar event: {e}")
            calendar_msg = " Meeting saved in database (calendar sync pending)."

        # Build response for the agent
        response = f"Meeting scheduled for {meeting_date}"
        if meeting_time:
            response += f" at {meeting_time}"
        if manager_name:
            response += f". Manager {manager_name} has been assigned."
        else:
            response += ". A manager will be assigned shortly."
        response += calendar_msg
        response += " Confirm the details with the caller."

        return response

    # ---- Create Agent Session ----
    session = AgentSession(
        allow_interruptions=True,
        min_interruption_duration=0.3,  # Faster interrupt detection
        min_interruption_words=1,
        min_endpointing_delay=0.3,      # Faster response time
        max_endpointing_delay=1.3,
        preemptive_generation=True,
        tools=[end_call, capture_outcome, schedule_callback, mark_as_dnc, schedule_meeting],
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
    await session.start(
        agent=VoiceAgent(
            is_outbound=is_outbound,
            phone_number=phone_number,
            contact_type=contact_type,
            caller_name_override=caller_name_override,
        ),
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


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="hookfish-voice-agent",
        )
    )
