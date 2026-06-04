"""
batch_call.py — Hookfish Batch Dispatch Core
=============================================
Shared engine used by batch_call_trigger.py and batch_call_campaign.py.
Also runnable standalone for ad-hoc lists.

Usage (standalone):
  python batch_call.py                        # call BATCH_NUMBERS as buyer
  python batch_call.py broker                 # call as broker
  python batch_call.py --name "Rahul"         # name override
  python batch_call.py --delay 30             # seconds between dispatch windows
  python batch_call.py --concurrency 3        # max simultaneous dispatches
  python batch_call.py --retries 5            # max retry attempts per number

Key guarantees:
  - asyncio.Semaphore caps live concurrent dispatches
  - Exponential backoff + jitter on dispatch failure (retryable errors only)
  - Non-retryable errors (DNC, daily cap, no name) fail immediately — no wasted retries
  - Dead-letter: every permanent failure is written to agent_batch_failures
  - Structured result dict returned for every number (dispatched/blocked/failed)
"""

import asyncio
import logging
import os
import json
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv
from livekit import api

from db_helper import (
    lookup_lead_by_phone,
    lookup_customer_by_phone,
    check_call_allowed,
    get_connection,
)

load_dotenv()

logger = logging.getLogger("batch-call")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

# ============================================================
# Configuration defaults (all overridable per run_batch() call)
# ============================================================
AGENT_NAME        = "hookfish-voice-agent"
CONTACT_TYPE      = "buyer"
DEFAULT_DELAY     = 10      # seconds between dispatch windows
DEFAULT_CONCURRENCY = 3     # max simultaneous live dispatches
DEFAULT_RETRIES   = 3       # max attempts per number before dead-letter
RETRY_BASE_SECS   = 2.0     # base backoff (doubles each attempt + jitter)

# Errors from LiveKit that are worth retrying (transient)
_RETRYABLE_SUBSTRINGS = (
    "timeout", "connection", "unavailable",
    "internal", "503", "502", "rate", "throttl",
)

# Ad-hoc list for standalone use
BATCH_NUMBERS = [
    "+916362185137",
    "+919930221107",
]

# ============================================================
# Result dataclass
# ============================================================
@dataclass
class DispatchResult:
    phone:   str
    status:  str          # "dispatched" | "blocked" | "failed"
    name:    str  = ""
    room:    str  = ""
    reason:  str  = ""
    attempts: int = 0
    error:   str  = ""


# ============================================================
# Dead-letter persistence
# ============================================================
def _ensure_failures_table() -> None:
    """Create agent_batch_failures if it doesn't exist. Called once at startup."""
    ddl = """
        CREATE TABLE IF NOT EXISTS agent_batch_failures (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            phone_number  VARCHAR(20)  NOT NULL,
            caller_name   VARCHAR(255),
            contact_type  VARCHAR(20)  DEFAULT 'buyer',
            target_project VARCHAR(255),
            error_message TEXT,
            attempts      INT          DEFAULT 0,
            batch_run_id  VARCHAR(64),
            created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
            retried_at    DATETIME     NULL,
            resolved      TINYINT(1)   DEFAULT 0,

            INDEX idx_phone   (phone_number),
            INDEX idx_run     (batch_run_id),
            INDEX idx_resolved (resolved)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
                conn.commit()
    except Exception as e:
        logger.warning(f"Could not create agent_batch_failures table: {e}")


def _write_dead_letter(
    phone: str,
    name: str,
    contact_type: str,
    target_project: Optional[str],
    error: str,
    attempts: int,
    batch_run_id: str,
) -> None:
    """Persist a permanently failed dispatch so it can be reviewed and re-queued."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_batch_failures
                        (phone_number, caller_name, contact_type, target_project,
                         error_message, attempts, batch_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        error_message = VALUES(error_message),
                        attempts      = VALUES(attempts),
                        retried_at    = NOW(),
                        resolved      = 0
                    """,
                    (phone, name, contact_type, target_project,
                     error, attempts, batch_run_id),
                )
                conn.commit()
        logger.debug(f"Dead-letter written for {phone}")
    except Exception as e:
        logger.error(f"Failed to write dead-letter for {phone}: {e}")


# ============================================================
# Single-call dispatch with retry
# ============================================================
async def dispatch_with_retry(
    phone:          str,
    contact_type:   str,
    caller_name:    Optional[str],
    target_project: Optional[str],
    semaphore:      asyncio.Semaphore,
    max_retries:    int,
    batch_run_id:   str,
    label:          str = "",
) -> DispatchResult:
    """
    Validate then dispatch one call, retrying transient failures with
    exponential backoff + jitter.  The semaphore caps concurrent dispatches.

    Returns a DispatchResult with status:
      "dispatched" — call is live
      "blocked"    — DNC / daily cap / no name (not retried)
      "failed"     — exhausted retries or non-retryable error
    """
    tag = f"[{label}] " if label else ""

    # ── 1. Pre-flight validation (no retry on these) ──────────────
    call_check = check_call_allowed(phone)
    if not call_check["allowed"]:
        reason = call_check["reason"]
        logger.info(f"{tag}{phone} BLOCKED — {reason}")
        return DispatchResult(phone=phone, status="blocked", reason=reason)

    # Resolve caller name from DB if not supplied
    resolved_name = caller_name
    if not resolved_name:
        customer = lookup_customer_by_phone(phone)
        if customer and customer.get("name"):
            resolved_name = customer["name"].strip()
        else:
            leads = lookup_lead_by_phone(phone)
            if leads and leads[0].get("partner_name"):
                resolved_name = leads[0]["partner_name"].strip()

    if not resolved_name:
        reason = "No contact name found in DB — use --name or add to DB"
        logger.warning(f"{tag}{phone} BLOCKED — {reason}")
        return DispatchResult(phone=phone, status="blocked", reason=reason)

    logger.info(f"{tag}{phone} → {resolved_name} ({contact_type})")

    # ── 2. Dispatch with semaphore + retry ────────────────────────
    last_error = ""
    for attempt in range(1, max_retries + 1):
        async with semaphore:
            room_name = (
                f"call-{int(time.time())}-"
                f"{''.join(str(random.randint(0, 9)) for _ in range(6))}"
            )
            metadata = json.dumps({
                "phone_number":   phone,
                "contact_type":   contact_type,
                "caller_name":    resolved_name,
                "target_project": target_project,
            })

            lkapi = api.LiveKitAPI(
                url=os.getenv("LIVEKIT_URL"),
                api_key=os.getenv("LIVEKIT_API_KEY"),
                api_secret=os.getenv("LIVEKIT_API_SECRET"),
            )
            try:
                await lkapi.agent_dispatch.create_dispatch(
                    api.CreateAgentDispatchRequest(
                        agent_name=AGENT_NAME,
                        room=room_name,
                        metadata=metadata,
                    )
                )
                logger.info(
                    f"{tag}{phone} DISPATCHED (attempt {attempt}) "
                    f"room={room_name}"
                )
                return DispatchResult(
                    phone=phone, status="dispatched",
                    name=resolved_name, room=room_name,
                    attempts=attempt,
                )

            except Exception as exc:
                last_error = str(exc)
                is_retryable = any(
                    s in last_error.lower() for s in _RETRYABLE_SUBSTRINGS
                )
                if not is_retryable or attempt == max_retries:
                    # Non-retryable or exhausted — fall through to dead-letter
                    logger.error(
                        f"{tag}{phone} FAILED after {attempt} attempt(s): {last_error}"
                    )
                    break

                # Exponential backoff with ±25 % jitter
                wait = RETRY_BASE_SECS * (2 ** (attempt - 1))
                wait *= random.uniform(0.75, 1.25)
                logger.warning(
                    f"{tag}{phone} attempt {attempt} failed ({last_error[:60]}…) "
                    f"— retrying in {wait:.1f}s"
                )
                await asyncio.sleep(wait)

            finally:
                await lkapi.aclose()

    # ── 3. Permanent failure → dead-letter ───────────────────────
    _write_dead_letter(
        phone=phone, name=resolved_name, contact_type=contact_type,
        target_project=target_project, error=last_error,
        attempts=max_retries, batch_run_id=batch_run_id,
    )
    return DispatchResult(
        phone=phone, status="failed", name=resolved_name,
        reason=last_error, attempts=max_retries, error=last_error,
    )


# ============================================================
# Batch runner
# ============================================================
async def run_batch(
    calls: list[dict],
    contact_type:    str  = CONTACT_TYPE,
    caller_name:     Optional[str] = None,
    target_project:  Optional[str] = None,
    delay:           float = DEFAULT_DELAY,
    concurrency:     int   = DEFAULT_CONCURRENCY,
    max_retries:     int   = DEFAULT_RETRIES,
    batch_run_id:    Optional[str] = None,
) -> list[DispatchResult]:
    """
    Dispatch a batch of calls with rate limiting and retry.

    Args:
        calls:          list of dicts with keys: phone, and optionally
                        name, contact_type, target_project (per-call overrides)
        contact_type:   default contact type ("buyer" | "broker")
        caller_name:    global name override (overrides DB lookup for all)
        target_project: default project to pitch
        delay:          seconds to wait between launching each call coroutine
        concurrency:    max simultaneous live dispatch calls (Semaphore limit)
        max_retries:    attempts before dead-lettering a number
        batch_run_id:   tag written to agent_batch_failures for tracing
    Returns:
        list of DispatchResult, one per input number
    """
    if not batch_run_id:
        batch_run_id = f"batch-{int(time.time())}"

    _ensure_failures_table()

    semaphore = asyncio.Semaphore(concurrency)
    total = len(calls)

    logger.info("=" * 58)
    logger.info("  Hookfish Batch Dispatch")
    logger.info("=" * 58)
    logger.info(f"  Numbers:     {total}")
    logger.info(f"  Type:        {contact_type}")
    logger.info(f"  Concurrency: {concurrency} simultaneous")
    logger.info(f"  Retries:     {max_retries} max per number")
    logger.info(f"  Delay:       {delay}s between launches")
    logger.info(f"  Run ID:      {batch_run_id}")
    logger.info("=" * 58)

    # Launch all coroutines, staggered by `delay` seconds between each
    tasks = []
    for i, call in enumerate(calls):
        phone    = call["phone"]
        c_type   = call.get("contact_type",   contact_type)
        c_name   = call.get("name",           caller_name)
        c_proj   = call.get("target_project", target_project)
        label    = f"{i + 1}/{total}"

        task = asyncio.create_task(
            dispatch_with_retry(
                phone=phone, contact_type=c_type, caller_name=c_name,
                target_project=c_proj, semaphore=semaphore,
                max_retries=max_retries, batch_run_id=batch_run_id,
                label=label,
            )
        )
        tasks.append(task)

        # Stagger launches — don't fire all coroutines simultaneously
        if i < total - 1:
            await asyncio.sleep(delay)

    results: list[DispatchResult] = await asyncio.gather(*tasks)

    # ── Summary ──────────────────────────────────────────────────
    dispatched = [r for r in results if r.status == "dispatched"]
    blocked    = [r for r in results if r.status == "blocked"]
    failed     = [r for r in results if r.status == "failed"]

    logger.info("=" * 58)
    logger.info(f"  BATCH COMPLETE  (run_id={batch_run_id})")
    logger.info(f"  Total:      {total}")
    logger.info(f"  Dispatched: {len(dispatched)}")
    logger.info(f"  Blocked:    {len(blocked)}")
    logger.info(f"  Failed:     {len(failed)}")
    if failed:
        logger.info(f"  Dead-letter: {len(failed)} written to agent_batch_failures")
    logger.info("=" * 58)

    for r in blocked:
        logger.info(f"  BLOCKED  {r.phone} — {r.reason}")
    for r in failed:
        logger.error(f"  FAILED   {r.phone} — {r.error[:80]}")

    return results


# ============================================================
# Standalone entry point
# ============================================================
def _parse_args(argv):
    ctype   = CONTACT_TYPE
    cname   = None
    delay   = DEFAULT_DELAY
    conc    = DEFAULT_CONCURRENCY
    retries = DEFAULT_RETRIES

    i = 0
    while i < len(argv):
        if argv[i] in ("buyer", "broker"):
            ctype = argv[i]; i += 1
        elif argv[i] == "--name"        and i + 1 < len(argv):
            cname = argv[i + 1];   i += 2
        elif argv[i] == "--delay"       and i + 1 < len(argv):
            delay = float(argv[i + 1]); i += 2
        elif argv[i] == "--concurrency" and i + 1 < len(argv):
            conc = int(argv[i + 1]);    i += 2
        elif argv[i] == "--retries"     and i + 1 < len(argv):
            retries = int(argv[i + 1]); i += 2
        else:
            i += 1
    return ctype, cname, delay, conc, retries


if __name__ == "__main__":
    ctype, cname, delay, conc, retries = _parse_args(sys.argv[1:])
    calls = [{"phone": p} for p in BATCH_NUMBERS]
    asyncio.run(
        run_batch(
            calls=calls,
            contact_type=ctype,
            caller_name=cname,
            delay=delay,
            concurrency=conc,
            max_retries=retries,
        )
    )