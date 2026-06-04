"""
batch_call_campaign.py — DB-driven campaign runner
===================================================
Pulls eligible leads from the database and dispatches outbound calls.
Delegates all dispatch, rate limiting, and retry logic to batch_call.run_batch().

Usage:
  python batch_call_campaign.py --dry-run              # preview without calling
  python batch_call_campaign.py --limit 20             # call next 20 eligible leads
  python batch_call_campaign.py --project "Maanikya"   # filter by project name
  python batch_call_campaign.py --concurrency 4        # 4 simultaneous dispatches
  python batch_call_campaign.py --retries 3            # retry each up to 3 times
"""

import asyncio
import sys
import logging
from dotenv import load_dotenv

from db_helper import get_connection, check_call_allowed
from batch_call import run_batch, DEFAULT_CONCURRENCY, DEFAULT_RETRIES, DEFAULT_DELAY

load_dotenv()
logger = logging.getLogger("batch-campaign")


# ============================================================
# Lead fetching
# ============================================================
def fetch_campaign_leads(limit: int = 20, project_filter: str = None) -> list[dict]:
    """
    Fetch eligible leads from the database.
    Returns a list of dicts with keys: phone, name, contact_type, target_project.
    DNC and daily-cap checks are applied per-number inside run_batch; here we
    just do a cheap DB-level filter on status.
    """
    leads = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT id, partner_phone, customer_phone,
                           partner_name, customer_name, property_name
                    FROM all_leads
                    WHERE deleted = 0 AND status != 'closed'
                """
                params = []
                if project_filter:
                    query += " AND property_name LIKE %s"
                    params.append(f"%{project_filter}%")
                query += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)

                cur.execute(query, tuple(params))
                leads = cur.fetchall()
    except Exception as e:
        logger.error(f"fetch_campaign_leads: {e}")
    return leads


def build_call_list(leads: list[dict], dry_run: bool) -> list[dict]:
    """
    Convert raw DB rows into the call-dict format expected by run_batch().
    Prints a dry-run preview; skips numbers with no phone.
    """
    calls = []
    for lead in leads:
        phone = lead.get("partner_phone") or lead.get("customer_phone")
        if not phone:
            continue
        name         = lead.get("partner_name") or lead.get("customer_name") or "Sir"
        contact_type = "broker" if lead.get("partner_phone") else "buyer"
        project      = lead.get("property_name")

        # Quick DNC + daily-cap pre-check so dry-run shows accurate counts
        call_check = check_call_allowed(phone)
        if not call_check["allowed"]:
            logger.info(f"[SKIP] {phone} ({name}) — {call_check['reason']}")
            continue

        calls.append({
            "phone":          phone,
            "name":           name,
            "contact_type":   contact_type,
            "target_project": project,
        })
        if dry_run:
            logger.info(f"[READY] {phone} ({name}) — {project}")

    return calls


# ============================================================
# Entry point
# ============================================================
async def run_campaign(
    limit:          int   = 20,
    project_filter: str   = None,
    dry_run:        bool  = False,
    concurrency:    int   = DEFAULT_CONCURRENCY,
    max_retries:    int   = DEFAULT_RETRIES,
    delay:          float = DEFAULT_DELAY,
) -> None:

    logger.info("=" * 60)
    logger.info("Hookfish Batch Call Campaign")
    logger.info("=" * 60)
    logger.info(f"Mode:           {'DRY RUN' if dry_run else 'LIVE CALLING'}")
    logger.info(f"Limit:          {limit}")
    logger.info(f"Project filter: {project_filter or 'none'}")
    logger.info(f"Concurrency:    {concurrency}")
    logger.info(f"Retries:        {max_retries}")
    logger.info("-" * 60)

    leads = fetch_campaign_leads(limit, project_filter)
    if not leads:
        logger.info("No eligible leads found.")
        return

    logger.info(f"Fetched {len(leads)} leads from DB. Validating...")
    calls = build_call_list(leads, dry_run)

    if not calls:
        logger.info("No callable leads after validation.")
        return

    if dry_run:
        logger.info(f"\nDry run complete. {len(calls)} numbers would be called.")
        return

    logger.info(f"\nProceeding to dispatch {len(calls)} calls...")
    await run_batch(
        calls=calls,
        delay=delay,
        concurrency=concurrency,
        max_retries=max_retries,
    )


if __name__ == "__main__":
    args = sys.argv[1:]

    is_dry_run = "--dry-run" in args
    limit      = 20
    project    = None
    conc       = DEFAULT_CONCURRENCY
    retries    = DEFAULT_RETRIES
    delay      = DEFAULT_DELAY

    i = 0
    while i < len(args):
        if   args[i] == "--limit"       and i + 1 < len(args): limit   = int(args[i+1]);   i += 2
        elif args[i] == "--project"     and i + 1 < len(args): project = args[i+1];         i += 2
        elif args[i] == "--concurrency" and i + 1 < len(args): conc    = int(args[i+1]);    i += 2
        elif args[i] == "--retries"     and i + 1 < len(args): retries = int(args[i+1]);    i += 2
        elif args[i] == "--delay"       and i + 1 < len(args): delay   = float(args[i+1]);  i += 2
        else: i += 1

    asyncio.run(run_campaign(
        limit=limit, project_filter=project, dry_run=is_dry_run,
        concurrency=conc, max_retries=retries, delay=delay,
    ))