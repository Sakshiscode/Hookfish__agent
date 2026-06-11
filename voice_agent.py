import asyncio
import logging
import os
import json
import re
import time
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv()

# Bypass proxy lookups on Windows (avoids slow HTTPx startup)
os.environ["NO_PROXY"] = "*"
os.environ["HTTPX_NO_PROXIES"] = "1"

from livekit.agents import JobContext, WorkerOptions, cli, get_job_context
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.llm import function_tool
from livekit.plugins import silero, deepgram, smallestai, openai, groq
from livekit import api, rtc

from db_helper import (
    get_connection,
    get_agent_context_optimized,
    lookup_customer_by_phone,
    save_call_outcome,
    create_call_log,
    update_call_log,
    record_call_attempt,
    allocate_manager,
    create_meeting,
    get_meetings_for_phone,
    update_meeting_calendar,
    get_manager_email,
    get_project_script,
    get_project_facts,
    add_project_details_column,
)
from db_helper import mark_dnc as db_mark_dnc

from google_calendar import schedule_meeting_on_calendar, parse_meeting_datetime, validate_calendar_config, CalendarConfigError

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-agent")
logging.getLogger("livekit.agents").setLevel(logging.DEBUG)

# Ensure properties.details column exists (safe no-op if already present)
add_project_details_column()

# Validate Google Calendar config at startup so misconfiguration surfaces
# immediately in the worker log rather than mid-call as an obscure auth failure.
# A CalendarConfigError means meetings will be DB-only until the config is fixed.
try:
    validate_calendar_config()
except CalendarConfigError as _cal_err:
    logger.warning(
        f"Google Calendar is misconfigured — meetings will be saved to DB only "
        f"until this is resolved.\n{_cal_err}"
    )

# ============================================================
# Constants
# ============================================================
MAX_CALL_DURATION = 240
CONTACT_TYPE_BUYER = "buyer"
CONTACT_TYPE_BROKER = "broker"


# ============================================================
# Database Context Builder
# ============================================================
def build_context_from_db(phone_number: str, pre_fetched_data: dict = None) -> str:
    context_parts = []
    data = pre_fetched_data or get_agent_context_optimized(phone_number)

    customer = data.get("customer")
    if customer:
        context_parts.append(
            f"Customer Info: Name = {customer['name']}, "
            f"ID = {customer['id']}, Source = {customer.get('origin', 'N/A')}"
        )

    leads = data.get("leads", [])
    if leads:
        context_parts.append(f"\nFound {len(leads)} leads for this number:")
        for lead in leads:
            info = (
                f"  - Lead #{lead['id']}: Customer = {lead['customer_name']}, "
                f"Property = {lead['property_name']}, Status = {lead['status']}"
            )
            if lead.get("notes"):
                info += f", Notes = {lead['notes']}"
            context_parts.append(info)

    projects = data.get("projects", [])
    if projects:
        context_parts.append("\nRelated Project/Property Details:")
        for proj in projects:
            info = f"  - {proj['name']}"
            if proj.get("type"):
                info += f" (Type: {proj['type']})"
            if proj.get("commission_percentage"):
                info += f" | Commission: {proj['commission_percentage']}%"
            context_parts.append(info)

    meetings = data.get("meetings", [])
    if meetings:
        context_parts.append("\nPast Meetings/Appointments:")
        for mtg in meetings:
            context_parts.append(
                f"  - {mtg['meeting_type']}: {mtg.get('meeting_date', 'TBD')} | Status: {mtg['status']}"
            )

    if not context_parts:
        context_parts.append(f"No previous information found for this number ({phone_number}).")

    return "\n".join(context_parts)


# ============================================================
# Agent Instructions
# ============================================================

# BASE_INSTRUCTIONS — persona, language rules, situation handling.
# No project-specific facts here. {caller_name} and {price_accuracy_block}
# are substituted at build time.
BASE_INSTRUCTIONS = """\
You are 'रिया' from Hookfish. A confident and professional Indian woman who works as a real estate calling executive.

VOICE & PERSONA:
- You are NOT a bot. You are a real person having a professional, respectful conversation.
- Your tone is polite and professional. Be courteous but get to the point.
- Use measured reactions: "जी बिल्कुल.", "अच्छा.", "जी सही बात है.", "जी मैं समझती हूँ."
- Keep responses SHORT (1-2 lines). Be informative, not chatty.
- Use natural feminine Hindi grammar ALWAYS:
  CORRECT: "मैं बोल रही हूँ", "मैं भेज दूँगी"
  WRONG: "मैं बोलता हूँ" (masculine — FORBIDDEN)

LANGUAGE RULES:
- Speak in natural Hindi (Devanagari script) mixed with English words.
- NEVER use romanized Hindi. Always Devanagari for Hindi words.
- All numbers, prices, floors, BHK sizes MUST be in English words.
  CORRECT: "two point fifty seven crore", "fifty lakh", "two BHK"
  WRONG: "ढाई करोड़", "पचास लाख"
- No markdown, no *, no %. Say "percent". NEVER use Hindi Purna Viram ("।"). Use English period (".").

{price_accuracy_block}

PROPERTY FACTS (DO NOT HALLUCINATE):
- Only state facts explicitly in the PROJECT SCRIPT below. NEVER invent details.
- If asked something not in the script: "ये detail मेरे पास अभी नहीं है. मैं confirm करके बताती हूँ."

SITUATIONS:
- NOT INTERESTED: "जी, कोई बात नहीं. अगर कभी future में property देखना हो तो call करिएगा. आपका शुक्रिया.." -> capture_outcome -> end_call
- NOT INTERESTED:  Wait and end the call.Do not repeat similar things while ending the call.
- BUYER SAYS "no thanks" / "nahi chahiye" / "interested nahi" -> acknowledge warmly -> capture_outcome -> end_call
- DNC: "जी बिल्कुल, disturb नहीं करूँगी. Sorry for the inconvenience." → mark_as_dnc → end_call
- WRONG NUMBER: "जी sorry, गलत number हो गया. माफ़ी चाहती हूँ." → end_call
- BUSY: "जी कोई बात नहीं. कब convenient रहेगा?" → schedule_callback
- SILENCE: "{caller_name} जी? Hello? सुन पा रहे हैं आप?"

CALL ENDING RULES:
- Wait and end the call.Do not repeat similar things while ending the call.
- NEVER say "main call end kar deti hoon" or "call yahi end hogi" or any variation.
- When ending: just say "जी, baat karke achha laga. Dhanyawad." and call end_call silently.
- The caller should never know the call is about to end until you say goodbye.

NAME USAGE: Address caller as "{caller_name} जी" throughout.
TOOL RULES: Tools run silently. When tool returns "[SILENT] Done." say NOTHING about it.
- NEVER narrate what you are about to do. Never say "main kahoongi ki..." or "call ends ho jaegi".
- NEVER describe your own actions out loud.
- Just DO the action naturally without announcing it.
"""

# BUYER_SCRIPT_TEMPLATE — used when no DB script exists for the project.
# {project_*} placeholders are substituted from DB facts or kept as
# generic prompts so the agent still functions without a DB record.
BUYER_SCRIPT_TEMPLATE = """
--- BUYER CALL FLOW ---

1. GREETING: "नमस्ते {caller_name} जी, कैसे हैं आप?" → wait

2. INTRO: "जी मैं रिया बोल रही हूँ Hookfish से. एक property opportunity के बारे में बताने के लिए call किया था. क्या एक minute है?" → wait

3. PITCH: "जी {caller_name} जी, {project_pitch_line} क्या और जानना चाहेंगे?" → wait

4. DETAILS: "{project_detail_lines} Site visit कब convenient रहेगा?"

5. KEY ANSWERS:
{project_key_answers}

6. CLOSE:
   - Interested → schedule_meeting
   - Maybe → schedule_callback
   - Not interested → address concern briefly → capture_outcome → end_call
--- END BUYER FLOW ---
"""
BROKER_SCRIPT_TEMPLATE = """
--- BROKER CALL FLOW ---

1. GREETING: "नमस्ते {caller_name} जी, कैसे हैं आप?" → wait

2. INTRO: "जी मैं रिया बोल रही हूँ Hookfish से. एक property opportunity है जो आपके clients के लिए useful हो सकती है. एक minute है?" → wait

3. PITCH: "जी {caller_name} जी, {project_broker_pitch} क्या और जानना चाहेंगे?" → wait

4. DETAILS: "{project_detail_lines} Site visit कब आ सकते हैं?"

5. Same key answers as buyer flow above.

6. QUALIFY: "आप currently किस area में काम कर रहे हैं?"
   - Interested → schedule_meeting
   - Maybe → "Details share कर दूँगी. Callback कब करूँ?" → schedule_callback
   - Not interested → "किस वजह से?" → capture_outcome → end_call
--- END BROKER FLOW ---
"""

INBOUND_INSTRUCTIONS = """
--- INBOUND CALL ---
1. "Hello, Hookfish में आपका स्वागत है. मैं रिया बोल रही हूँ. कैसे मदद करूँ?"
2. Listen and respond accordingly.
3. Complaint → "हमारी team एक दिन में call back करेगी."
--- END INBOUND ---
"""

# Fallback facts used when properties.details has no entry for a field.
# Keeps the agent functional even if the DB has no record for the project.
_FALLBACK_FACTS = {
    "project_name":          "माणिक्य",
    "developer":             "Viyan Ventures",
    "location":              "माहिम West",
    "bhk_type":              "Two BHK",
    "total_floors":          "G plus twenty two storey",
    "price_smaller":         "two point fifty seven crore",
    "price_larger":          "two point sixty seven crore",
    "payment_plan":          "fifty lakh अभी, possession तक कोई payment नहीं",
    "unit_sizes":            "six hundred four और six hundred nineteen square feet RERA carpet area",
    "construction_progress": "Eleventh slab complete out of twenty three. BMC CC eighteenth floor तक.",
    "available_floors":      "Thirteenth floor और ऊपर",
    "rera_possession":       "twenty twenty nine",
    "construction_company":  "Miven Technology",
    "landmark":              "Jimmy Boy Bakery के opposite, Bank Of Baroda, Desai Park",
    "commission":            None,   # filled from properties.commission_percentage
    "site_visit_bonus":      None,   # filled from properties.site_visit_bonus
}


def _resolve_facts(target_project: str | None, db_projects: list) -> dict:
    """
    Build a complete facts dict for the target project.

    Priority order:
      1. properties.details JSON  (get_project_facts)
      2. properties scalar columns (commission_percentage, site_visit_bonus)
      3. _FALLBACK_FACTS hardcoded defaults
    """
    facts = dict(_FALLBACK_FACTS)

    # Try to get structured facts from the DB
    project_name = target_project or (db_projects[0]["name"] if db_projects else None)
    if project_name:
        db_facts = get_project_facts(project_name)
        if db_facts:
            # Scalar columns
            if db_facts.get("commission_percentage"):
                facts["commission"] = str(db_facts["commission_percentage"])
            if db_facts.get("site_visit_bonus"):
                facts["site_visit_bonus"] = str(db_facts["site_visit_bonus"])
            if db_facts.get("name"):
                facts["project_name"] = db_facts["name"]

            # JSON details column — individual fields override fallback
            details = db_facts.get("details") or {}
            for key in _FALLBACK_FACTS:
                if key in details and details[key]:
                    facts[key] = details[key]

    return facts


def _build_price_accuracy_block(facts: dict) -> str:
    """
    Build the PRICE ACCURACY block from resolved facts.
    This replaces the hardcoded prices in BASE_INSTRUCTIONS.
    """
    smaller = facts.get("price_smaller", "two point fifty seven crore")
    larger  = facts.get("price_larger",  "two point sixty seven crore")
    return (
        f"PRICE ACCURACY (CRITICAL — DO NOT VIOLATE):\n"
        f"- Exact price: {smaller} (smaller unit) and {larger} (larger unit).\n"
        f"- NEVER say any other price. NEVER approximate."
    )


def _build_key_answers(facts: dict) -> str:
    """Build the KEY ANSWERS block from resolved facts."""
    lines = [
        f"   - Floors: \"{facts['total_floors']}. {facts['construction_progress']}\"",
        f"   - Size: \"{facts['unit_sizes']}\"",
        f"   - Price: \"{facts['price_smaller']} smaller unit, {facts['price_larger']} larger unit. "
        f"All inclusive — agreement, stamp duty six percent, GST five percent, all charges included.\"",
        f"   - Payment: \"{facts['payment_plan']}. Stamp duty और GST pay करके register भी करा सकते हैं.\"",
        f"   - Location: \"{facts['location']}, {facts['landmark']}.\"",
        f"   - Available floors: \"{facts['available_floors']} new buyers के लिए.\"",
        f"   - Possession: \"RERA possession {facts['rera_possession']}.\"",
    ]
    if facts.get("commission"):
        lines.append(f"   - Commission: \"{facts['commission']} percent brokerage available.\"")
    if facts.get("site_visit_bonus"):
        lines.append(f"   - Site visit bonus: \"Site visit bonus of {facts['site_visit_bonus']}.\"")
    return "\n".join(lines)


def _build_pitch_lines(facts: dict, contact_type: str) -> dict:
    """Build the pitch, detail, and broker-pitch strings from facts."""
    f = facts
    pitch = (
        f"हम {f['location']} में एक नया project लाए हैं - '{f['project_name']}' by {f['developer']}. "
        f"South Bombay का prime location है. Station और Metro दोनों walking distance पर हैं. "
        f"{f['bhk_type']} apartments हैं. Payment plan बहुत attractive है — "
        f"अभी सिर्फ {f['payment_plan']}."
    )
    broker_pitch = (
        f"{f['location']} में '{f['project_name']}' by {f['developer']}. "
        f"South Bombay prime location. {f['bhk_type']}. {f['payment_plan']}. "
        f"Brokerage भी attractive है."
    )
    detail = (
        f"जी, ये redevelopment project है. {f['construction_company']} से construction हो रही है. "
        f"{f['construction_progress']} New buyers के लिए {f['available_floors']} available है. "
        f"RERA possession {f['rera_possession']} है."
    )
    return {"pitch": pitch, "broker_pitch": broker_pitch, "detail": detail}


def build_agent_instructions(
    is_outbound: bool = False,
    phone_number: str = None,
    contact_type: str = CONTACT_TYPE_BUYER,
    caller_name_override: str = None,
    target_project: str = None,
    pre_fetched_data: dict = None,
) -> str:
    """
    Build the complete system prompt for the voice agent.

    Resolution order for project facts:
      1. properties.details JSON column  (most specific, DB-managed)
      2. properties scalar columns       (commission, bonuses)
      3. _FALLBACK_FACTS                 (hardcoded defaults, last resort)

    Resolution order for call script:
      1. agent_scripts.content matching target_project (DB-managed script)
      2. Rendered BUYER_SCRIPT_TEMPLATE / BROKER_SCRIPT_TEMPLATE with facts
    """
    # ── 1. Caller name ────────────────────────────────────────────
    caller_name = "Sir/Ma'am"
    data = pre_fetched_data or {}

    if phone_number:
        customer = data.get("customer")
        if customer and customer.get("name"):
            caller_name = customer["name"].strip()
        else:
            leads = data.get("leads", [])
            if leads and leads[0].get("partner_name"):
                caller_name = leads[0]["partner_name"].strip()

    if caller_name_override:
        caller_name = caller_name_override

    # ── 2. Resolve project facts ──────────────────────────────────
    db_projects = data.get("projects", [])
    facts = _resolve_facts(target_project, db_projects)

    # ── 3. Build base instructions (persona + dynamic price block) ─
    price_block = _build_price_accuracy_block(facts)
    instructions = (
        BASE_INSTRUCTIONS
        .replace("{caller_name}", caller_name)
        .replace("{price_accuracy_block}", price_block)
    )

    # ── 4. Build call script ──────────────────────────────────────
    if not is_outbound:
        instructions += INBOUND_INSTRUCTIONS.replace("{caller_name}", caller_name)

    else:
        # Try to fetch a full script from agent_scripts table first
        db_script = get_project_script(
            target_project or facts["project_name"],
            contact_type,
        )

        if db_script:
            # DB script: substitute caller name and any {{fact}} tokens
            script_section = db_script.replace("{caller_name}", caller_name)
            for key, value in facts.items():
                if value:
                    script_section = script_section.replace(f"{{{{{key}}}}}", value)
            instructions += f"\n--- PROJECT SCRIPT ---\n{script_section}\n--- END SCRIPT ---\n"

        else:
            # No DB script: render the appropriate template with resolved facts
            pitch_lines = _build_pitch_lines(facts, contact_type)
            key_answers = _build_key_answers(facts)

            if contact_type == CONTACT_TYPE_BROKER:
                script_section = (
                    BROKER_SCRIPT_TEMPLATE
                    .replace("{caller_name}", caller_name)
                    .replace("{project_broker_pitch}", pitch_lines["broker_pitch"])
                    .replace("{project_detail_lines}", pitch_lines["detail"])
                    .replace("{project_key_answers}", key_answers)
                )
            else:
                script_section = (
                    BUYER_SCRIPT_TEMPLATE
                    .replace("{caller_name}", caller_name)
                    .replace("{project_pitch_line}", pitch_lines["pitch"])
                    .replace("{project_detail_lines}", pitch_lines["detail"])
                    .replace("{project_key_answers}", key_answers)
                )
            instructions += script_section

    # ── 5. DB context (lead history, past meetings) ───────────────
    db_context = build_context_from_db(phone_number, pre_fetched_data=pre_fetched_data) if phone_number else ""

    if target_project:
        db_context += (
            f"\n\n*** TARGET PROJECT ***\n"
            f"Project: {target_project}\n"
            f"STRICT RULE: ONLY PITCH THIS PROJECT. Do not discuss any other project.\n"
        )

    if db_context.strip() and "No previous information" not in db_context:
        instructions += f"\n\n--- DATABASE CONTEXT ---\n{db_context.strip()}\n---\n"
    return instructions


# ============================================================
# Voice Agent Class
# ============================================================

class FilteredTTS(smallestai.TTS):
    """
    Strips LLM function-call artifacts before they reach the TTS engine.

    The LLM occasionally leaks internal tool-use text into the speech stream,
    which sounds absurd to the caller. We apply three layers of defence:

    1. Known-label strip  — removes any text starting from a recognisable
       function-call keyword (covers the common case where the model names
       its own tool or emits structured labels).
    2. JSON block strip   — removes {...} or [...] blocks that survived the
       first pass (raw JSON that the model sometimes emits as a preamble).
    3. Bracket cleanup    — removes dangling parenthetical or square-bracket
       fragments that contain no natural-language words (e.g. "(outcome:
       interested)" leftover after the JSON was stripped).

    If all text is stripped the TTS receives a single space so it doesn't
    error on an empty string.
    """

    # Recognisable tool/function labels — extended beyond the original set
    

    _LABEL_PATTERN = re.compile(
        r'(?i)('
        r'|\bfunction\b[\s\S]*'
        # 'function' followed by toolname or newline
        r'\bfunction\b[\s]+(?:end_call|capture_outcome|schedule_callback|schedule_meeting|mark_as_dnc)[\s\S]*'
        r'|\bfunction\b[\s]*[\n\r][\s\S]*'
        r'|\bfunction\b\s*$'
        # Tool names standalone
        r'|\bend_call\b[\s\S]*'
        r'|\bcapture_outcome\b[\s\S]*'
        r'|\bschedule_callback\b[\s\S]*'
        r'|\bschedule_meeting\b[\s\S]*'
        r'|\bmark_as_dnc\b[\s\S]*'
        # function call/name combos
        r'|\bfunction[\s_]?(?:call|name|calls|arguments?)\b[\s\S]*'
        r'|\btool[\s_]?(?:use|call|name)\b[\s\S]*'
        # Parameter labels
        r'|\boutcome[\s_]?reason\b[\s\S]*'
        r'|\blead[\s_]?score\b[\s\S]*'
        r'|\bnext[\s_]?action\b[\s\S]*'
        r'|\binterest[\s_]?level\b[\s\S]*'
        # XML and JSON
        r'|<tool_call>[\s\S]*?</tool_call>'
        r'|<function_calls>[\s\S]*'
        r'|\"name\":\s*\"[a-z_]+\"[\s\S]*'
        r'|\"arguments\":\s*\{[\s\S]*'
        r')',
        re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )

    _JSON_PATTERN = re.compile(r'[\[{][^)\]]*[\]}]', re.DOTALL)
    _BRACKET_JUNK = re.compile(r'[\(\[（【][^a-zA-Z\u0900-\u097F]{0,5}[\)\]）】]')

    def synthesize(self, text: str, **kwargs):
        cleaned = text.replace('₹', '').replace('।', '.').strip()
        cleaned = cleaned.replace('फंक्शन', '').replace('फ़ंक्शन', '')
        cleaned = self._LABEL_PATTERN.sub('', text).strip()
        cleaned = self._JSON_PATTERN.sub('', cleaned).strip()
        cleaned = self._BRACKET_JUNK.sub('', cleaned).strip()
        cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
        if not cleaned:
            cleaned = " "
        return super().synthesize(cleaned, **kwargs)


class VoiceAgent(Agent):
    def __init__(self, instructions: str, is_outbound: bool = False,
                 phone_number: str = None, contact_type: str = CONTACT_TYPE_BUYER) -> None:

        super().__init__(
            instructions=instructions,
            vad=silero.VAD.load(
                min_silence_duration=0.2,
                min_speech_duration=0.1,
            ),
            stt=deepgram.STT(
                model="nova-3",
                language="hi",
                interim_results=True,
                smart_format=False,
            ),
            #llm=groq.LLM(
            #    model= "llama-3.3-70b-versatile",#"llama-3.1-8b-instant"    #"llama-3.3-70b-versatile",
            #),
            llm=openai.LLM(
                model="gpt-4o-mini",
                api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
                base_url="https://audiobot.services.ai.azure.com/openai/v1",
            ),
            tts=FilteredTTS(
                model="lightning-v3.1",
                voice_id="maithili",
                language="hi",
                sample_rate=24000,
                #base_url="https://api.smallest.ai/waves/v1",
            ),
            min_endpointing_delay=0.25,
            allow_interruptions=True,
        )
        self._is_outbound = is_outbound
        self._phone_number = phone_number
        self._contact_type = contact_type

    async def on_enter(self):
        if not self._is_outbound:
            await self.session.generate_reply()


# ============================================================
# SIP Identity Helper
# ============================================================

def extract_phone_from_identity(identity: str) -> str:
    if not identity:
        return None
    sip_match = re.search(r'sip:(\+?\d+)@', identity)
    if sip_match:
        n = sip_match.group(1)
        return n if n.startswith('+') else '+' + n
    phone_match = re.match(r'(\+?\d{10,15})$', identity)
    if phone_match:
        n = phone_match.group(1)
        return n if n.startswith('+') else '+' + n
    return None


# ============================================================
# Entrypoint
# ============================================================

async def entrypoint(ctx: JobContext):
    logger.info(f"Room connected: {ctx.room.name}")

    phone_number = None
    is_outbound = False
    contact_type = CONTACT_TYPE_BUYER
    sip_trunk_id = os.getenv("TRUNK_ID", "")
    caller_name_override = None
    call_log_id = None
    target_project = None
    caller_name = "Sir/Ma'am"

    # ---- Parse metadata ----
    if ctx.job.metadata:
        try:
            dial_info = json.loads(ctx.job.metadata)
            phone_number = dial_info.get("phone_number")
            contact_type = dial_info.get("contact_type", CONTACT_TYPE_BUYER)
            caller_name_override = dial_info.get("caller_name")
            target_project = dial_info.get("target_project")
            if phone_number:
                is_outbound = True
        except json.JSONDecodeError:
            pass

    # ---- Pre-fetch DB data ----
    db_data = {"allowed": True, "reason": "OK", "customer": None, "leads": [], "projects": [], "meetings": []}
    if phone_number:
        logger.info(f"Pre-fetching DB data for {phone_number}...")
        db_data = await asyncio.to_thread(get_agent_context_optimized, phone_number)

    # ---- Outbound validation ----
    if is_outbound and phone_number:
        if not db_data.get("allowed", True):
            logger.warning(f"Call blocked to {phone_number}: {db_data['reason']}")
            ctx.shutdown(reason=f"call_blocked: {db_data['reason']}")
            return

        if caller_name_override:
            caller_name = caller_name_override
        else:
            customer = db_data.get("customer")
            if customer and customer.get("name"):
                caller_name = customer["name"].strip()
            else:
                leads = db_data.get("leads", [])
                if leads and leads[0].get("partner_name"):
                    caller_name = leads[0]["partner_name"].strip()

    # ---- Create call log ----
    if phone_number:
        call_log_id = create_call_log(
            call_id=ctx.room.name,
            phone_number=phone_number,
            caller_name=caller_name_override or caller_name,
            contact_type=contact_type,
            direction="outbound" if is_outbound else "inbound",
            room_name=ctx.room.name,
        )
        record_call_attempt(phone_number, result="initiated", call_log_id=call_log_id)

    # ---- Build instructions ----
    agent_instructions = await asyncio.to_thread(
        build_agent_instructions,
        is_outbound, phone_number, contact_type,
        caller_name_override, target_project, db_data,
    )

    # ---- Connect to room ----
    await ctx.connect()

    # ---- Inbound: wait for SIP participant ----
    if not is_outbound:
        for participant in ctx.room.remote_participants.values():
            extracted = extract_phone_from_identity(participant.identity)
            if extracted:
                phone_number = extracted
                break

        if not phone_number:
            try:
                event = asyncio.Event()
                found = {"number": None}

                def on_connected(p: rtc.RemoteParticipant):
                    n = extract_phone_from_identity(p.identity)
                    if n:
                        found["number"] = n
                    event.set()

                ctx.room.on("participant_connected", on_connected)
                await asyncio.wait_for(event.wait(), timeout=30.0)
                phone_number = found["number"]
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for inbound participant.")

        if phone_number and not call_log_id:
            db_data = await asyncio.to_thread(get_agent_context_optimized, phone_number)
            call_log_id = create_call_log(
                call_id=ctx.room.name,
                phone_number=phone_number,
                contact_type=contact_type,
                direction="inbound",
                room_name=ctx.room.name,
            )

    # ---- Transcript & timing ----
    transcript_messages = []
    call_start_time = time.time()
    MIN_CALL_SECS = 30  # block tools for first 30s to prevent premature end

    # ---- Function Tools ----

    @function_tool(name="end_call", description= "End the call ONLY when caller explicitly says goodbye words: 'bye', 'goodbye', 'band karo', 'rakhna hai', 'phone rakhta hoon', 'alvida', 'dhanyawad bye'. 'Chalega', 'theek hai', 'ok', 'haan' are NOT goodbye — after site visit scheduled, ask 'Koi aur sawaal hai?' and wait. NEVER end just because site visit was scheduled."
)
    async def end_call(reason: str = "call_completed") -> str:
        if time.time() - call_start_time < MIN_CALL_SECS:
            return ""
        logger.info(f"end_call: {reason}")
        if call_log_id:
            try:
                update_call_log(
                    call_log_id,
                    duration_seconds=int(time.time() - call_start_time),
                    transcript="\n".join(f"{m['role']}: {m['text']}" for m in transcript_messages),
                    notes=f"Ended: {reason}",
                )
            except Exception as e:
                logger.error(f"end_call log error: {e}")

        async def _shutdown():
            await asyncio.sleep(3)
            try:
                job_ctx = get_job_context()
                await job_ctx.delete_room()
                job_ctx.shutdown(reason="end_call")
            except Exception as e:
                logger.error(f"shutdown error: {e}")

        asyncio.create_task(_shutdown())
        return "[SILENT] Done. Say goodbye naturally."

    @function_tool(
        name="capture_outcome",
        description="Save call outcome. ONLY use when call is ending — NEVER at start. outcome: 'interested'/'maybe'/'not_interested'/'callback_requested'/'dnc'/'wrong_number'. interest_level: 'high'/'medium'/'low'/'none'.",
    )
    async def capture_outcome(
        outcome: str,
        reason: str = None,
        interest_level: str = "unknown",
        next_action: str = None,
    ) -> str:
        if time.time() - call_start_time < MIN_CALL_SECS:
            return ""
        logger.info(f"capture_outcome: {outcome}, interest={interest_level}")
        if phone_number:
            save_call_outcome(phone=phone_number, outcome=outcome, reason=reason,
                              interest_level=interest_level, next_action=next_action)
        if call_log_id:
            update_call_log(
                call_log_id,
                disposition={"interested":"qualified","maybe":"follow_up","not_interested":"rejected",
                             "callback_requested":"callback","dnc":"dnc","wrong_number":"wrong_number"}.get(outcome, outcome),
                qualification=outcome, interest_level=interest_level,
                outcome_reason=reason, next_action=next_action,
            )
        return "[SILENT] Done."

    @function_tool(name="schedule_callback", description="Schedule a callback when user asks to be called later.")
    async def schedule_callback(
        callback_date: str = None,
        callback_time: str = None,
        notes: str = None,
    ) -> str:
        if time.time() - call_start_time < MIN_CALL_SECS:
            return ""
        logger.info(f"schedule_callback: {callback_date} {callback_time}")
        if phone_number:
            save_call_outcome(phone=phone_number, outcome="callback_requested",
                              next_action="callback",
                              callback_date=f"{callback_date or ''} {callback_time or ''}".strip())
        if call_log_id:
            update_call_log(call_log_id, next_action="callback",
                            notes=f"Callback: {callback_date} {callback_time}")
        return "[SILENT] Done."

    @function_tool(name="mark_as_dnc", description="Mark number as Do Not Call. Use ONLY when caller explicitly asks to be removed.")
    async def mark_as_dnc(reason: str = "user_requested") -> str:
        if time.time() - call_start_time < MIN_CALL_SECS:
            return ""
        logger.info(f"mark_as_dnc: {phone_number}")
        if phone_number:
            db_mark_dnc(phone_number, reason=reason, added_by="agent")
        if call_log_id:
            update_call_log(call_log_id, disposition="dnc", notes=f"DNC: {reason}")
        return "[SILENT] Done."

    @function_tool(name="schedule_meeting", description="Schedule a site visit or meeting when buyer/broker agrees.")
    async def schedule_meeting(
        meeting_date: str = None,
        meeting_time: str = None,
        meeting_type: str = "site_visit",
        project_name: str = None,
        location: str = None,
        notes: str = None,
    ) -> str:
        if time.time() - call_start_time < MIN_CALL_SECS:
            return ""
        logger.info(f"schedule_meeting: {meeting_date} {meeting_time}")

        try:
            start_dt, _ = parse_meeting_datetime(meeting_date, meeting_time)
            clean_date = start_dt.strftime("%Y-%m-%d")
            clean_time = start_dt.strftime("%H:%M:%S")
        except Exception:
            clean_date, clean_time = meeting_date, meeting_time

        async def _bg():
            errors = []

            # 1. Allocate manager
            try:
                manager = await asyncio.to_thread(allocate_manager, project_name)
            except Exception as e:
                logger.error(f"schedule_meeting: allocate_manager failed: {e}")
                manager = None

            mgr_name = manager["name"] if manager else None
            mgr_id   = manager["id"]   if manager else None

            # Resolve caller name
            c_name = caller_name_override
            if not c_name and phone_number:
                try:
                    c = await asyncio.to_thread(lookup_customer_by_phone, phone_number)
                    if c:
                        c_name = c.get("name")
                except Exception as e:
                    logger.error(f"schedule_meeting: lookup_customer failed: {e}")

            # 2. Save meeting to DB
            mtg_id = None
            if phone_number:
                try:
                    mtg_id = await asyncio.to_thread(
                        create_meeting,
                        phone_number=phone_number, contact_name=c_name,
                        contact_type=contact_type, meeting_type=meeting_type,
                        meeting_date=clean_date, meeting_time=clean_time,
                        location=location, project_name=project_name,
                        manager_id=mgr_id, manager_name=mgr_name,
                        call_log_id=call_log_id, notes=notes,
                    )
                    if not mtg_id:
                        errors.append("meeting DB record")
                except Exception as e:
                    logger.error(f"schedule_meeting: create_meeting failed: {e}")
                    errors.append("meeting DB record")

            # 3. Update call log
            if call_log_id:
                try:
                    await asyncio.to_thread(
                        update_call_log, call_log_id,
                        next_action="site_visit",
                        manager_assigned=mgr_name,
                        notes=f"Meeting: {clean_date} {clean_time} | {mgr_name or 'TBD'}",
                    )
                except Exception as e:
                    logger.error(f"schedule_meeting: update_call_log failed: {e}")

            # 4. Create Google Calendar event
            try:
                mgr_email = None
                if mgr_id:
                    mgr_email = await asyncio.to_thread(get_manager_email, mgr_id)
                cal = await asyncio.to_thread(
                    schedule_meeting_on_calendar,
                    contact_name=c_name or "Contact",
                    contact_phone=phone_number or "",
                    contact_type=contact_type, meeting_type=meeting_type,
                    date_str=meeting_date, time_str=meeting_time,
                    project_name=project_name, location=location,
                    manager_name=mgr_name or "", manager_email=mgr_email or "",
                    notes=notes,
                )
                if cal.get("success") and mtg_id:
                    await asyncio.to_thread(update_meeting_calendar, mtg_id, cal["event_id"])
                elif not cal.get("success"):
                    errors.append("calendar invite")
                    logger.error(f"schedule_meeting: calendar failed: {cal.get('error')}")
            except Exception as e:
                logger.error(f"schedule_meeting: schedule_meeting_on_calendar failed: {e}")
                errors.append("calendar invite")

            if errors:
                logger.warning(
                    f"schedule_meeting for {phone_number} completed with failures: {errors}. "
                    f"meeting_id={mtg_id}, manager={mgr_name}"
                )

        asyncio.create_task(_bg())
        return "[SILENT] Done."

    # ---- Session ----
    session = AgentSession(
        allow_interruptions=True,
        min_interruption_duration=0.8,
        min_interruption_words=2,
        min_endpointing_delay=0.1,
        max_endpointing_delay=0.6,
        preemptive_generation=True,
        #chat_ctx_size=8,
        tools=[end_call, capture_outcome, schedule_callback, mark_as_dnc, schedule_meeting],
    )

    @session.on("agent_speech_committed")
    def on_agent_speech(msg):
        transcript_messages.append({"role": "agent", "text": str(msg)})

    @session.on("user_speech_committed")
    def on_user_speech(msg):
        transcript_messages.append({"role": "user", "text": str(msg)})

    voice_agent = VoiceAgent(
        instructions=agent_instructions,
        is_outbound=is_outbound,
        phone_number=phone_number,
        contact_type=contact_type,
    )

    await session.start(agent=voice_agent, room=ctx.room)

    # ---- Outbound: place SIP call ----
    if is_outbound and phone_number:
        logger.info(f"Placing outbound call to {phone_number}...")
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
            logger.info("Call answered. Triggering greeting.")
            record_call_attempt(phone_number, result="answered", call_log_id=call_log_id)

            async def _greet():
                await asyncio.sleep(0.5)
                try:
                    await voice_agent.session.say(
                        f"नमस्ते {caller_name} जी, कैसे हैं आप?",
                        add_to_chat_ctx=True,
                    )
                except Exception as e:
                    logger.error(f"Greeting error: {e}")
                    await voice_agent.session.generate_reply()

            asyncio.create_task(_greet())

        except Exception as e:
            logger.error(f"SIP call error: {e}")
            record_call_attempt(phone_number, result="failed", call_log_id=call_log_id)
            if call_log_id:
                update_call_log(call_log_id, disposition="failed", notes=str(e))
            ctx.shutdown()
        finally:
            await lkapi.aclose()

    # ---- Auto-end timer ----
    async def _auto_end():
        await asyncio.sleep(MAX_CALL_DURATION)
        logger.info("Max duration reached. Auto-ending.")
        if call_log_id:
            try:
                update_call_log(
                    call_log_id,
                    disposition="max_duration",
                    duration_seconds=MAX_CALL_DURATION,
                    transcript="\n".join(f"{m['role']}: {m['text']}" for m in transcript_messages),
                    notes="Auto-ended: max duration",
                )
            except Exception as e:
                logger.error(f"auto-end log error: {e}")
        try:
            job_ctx = get_job_context()
            await job_ctx.delete_room()
            job_ctx.shutdown(reason="max_duration_reached")
        except Exception as e:
            logger.error(f"auto-end shutdown error: {e}")

    asyncio.create_task(_auto_end())


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="hookfish-voice-agent",
        )
    )