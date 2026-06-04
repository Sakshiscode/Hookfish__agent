"""
batch_call_trigger.py — Ad-hoc parallel batch dispatch
=======================================================
Dispatches the Hookfish voice agent to a fixed list of numbers.
All calls are launched concurrently (each gets its own room + agent)
with a semaphore capping simultaneous dispatches and full retry logic.

Delegates to batch_call.run_batch() — see that module for full docs.

Usage:
  python batch_call_trigger.py                        # call BATCH_NUMBERS as buyer
  python batch_call_trigger.py broker                 # call as broker
  python batch_call_trigger.py --delay 5              # 5s stagger between launches
  python batch_call_trigger.py --concurrency 5        # allow 5 simultaneous dispatches
  python batch_call_trigger.py --retries 4            # retry each number up to 4 times
"""

import asyncio
import sys
from batch_call import run_batch, CONTACT_TYPE, DEFAULT_DELAY, DEFAULT_CONCURRENCY, DEFAULT_RETRIES, _parse_args

# ── Numbers to call ───────────────────────────────────────────
BATCH_NUMBERS = [
    "+917039853851",
    "+918468857601",
    "+919819876103",
    "+919619755450",
]

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