"""
Vobiz + LiveKit SIP Trunk Setup Script
=======================================
This script creates the outbound and inbound SIP trunks in LiveKit
to connect with your Vobiz account.

Usage:
  1. Fill in the VOBIZ_* values below from your Vobiz console
  2. Run: python setup_vobiz_trunks.py
"""

import asyncio
import os
from dotenv import load_dotenv
from livekit import api

load_dotenv()

# ============================================================
# ⚠️  FILL THESE IN from your Vobiz Console
# ============================================================

# From Vobiz Outbound Trunk creation:
VOBIZ_SIP_DOMAIN = "95b93a49.sip.vobiz.ai"       # e.g. "5f3a607b.sip.vobiz.ai"
VOBIZ_USERNAME = "eshaan0309"          # e.g. "user_abc123"
VOBIZ_PASSWORD = "esh@@n03092004"          # e.g. "pass_xyz789"

# Your Vobiz phone number (with country code):
VOBIZ_PHONE_NUMBER = "+912271264169"      # e.g. "+918071387434"

# ============================================================


async def setup_trunks():
    lk = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    # --- Validate inputs ---
    if not all([VOBIZ_SIP_DOMAIN, VOBIZ_USERNAME, VOBIZ_PASSWORD, VOBIZ_PHONE_NUMBER]):
        print("❌ Please fill in all VOBIZ_* values at the top of this script!")
        print("   - VOBIZ_SIP_DOMAIN")
        print("   - VOBIZ_USERNAME")
        print("   - VOBIZ_PASSWORD")
        print("   - VOBIZ_PHONE_NUMBER")
        return

    try:
        # =============================================
        # STEP 1: Create Outbound Trunk
        # =============================================
        print("=" * 50)
        print("STEP 1: Creating Outbound SIP Trunk...")
        print("=" * 50)

        outbound_trunk = await lk.sip.create_sip_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(
                trunk=api.SIPOutboundTrunkInfo(
                    name="Vobiz Outbound Trunk",
                    address=VOBIZ_SIP_DOMAIN,
                    auth_username=VOBIZ_USERNAME,
                    auth_password=VOBIZ_PASSWORD,
                    numbers=[VOBIZ_PHONE_NUMBER],
                )
            )
        )

        outbound_trunk_id = outbound_trunk.sip_trunk_id
        print(f"✅ Outbound trunk created!")
        print(f"   Trunk ID: {outbound_trunk_id}")
        print()

        # =============================================
        # STEP 2: Create Inbound Trunk
        # =============================================
        print("=" * 50)
        print("STEP 2: Creating Inbound SIP Trunk...")
        print("=" * 50)

        inbound_trunk = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name="Vobiz Inbound Trunk",
                    numbers=[VOBIZ_PHONE_NUMBER],
                    allowed_addresses=["0.0.0.0/0"],  # Allow all (restrict in production)
                )
            )
        )

        inbound_trunk_id = inbound_trunk.sip_trunk_id
        print(f"✅ Inbound trunk created!")
        print(f"   Trunk ID: {inbound_trunk_id}")
        print()

        # =============================================
        # STEP 3: Create Dispatch Rule for Inbound
        # =============================================
        print("=" * 50)
        print("STEP 3: Creating Dispatch Rule...")
        print("=" * 50)

        dispatch_rule = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                trunk_ids=[inbound_trunk_id],
                rule=api.SIPDispatchRule(
                    dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                        room_prefix="call-",
                    )
                ),
                name="Vobiz Inbound Dispatch",
                metadata="",
                attributes={},
            )
        )

        print(f"✅ Dispatch rule created!")
        print(f"   Rule ID: {dispatch_rule.sip_dispatch_rule_id}")
        print()

        # =============================================
        # SUMMARY
        # =============================================
        print("=" * 50)
        print("🎉 SETUP COMPLETE!")
        print("=" * 50)
        print()
        print("Save these values:")
        print(f"  OUTBOUND Trunk ID: {outbound_trunk_id}")
        print(f"  INBOUND Trunk ID:  {inbound_trunk_id}")
        print()
        print("Next steps:")
        print(f"  1. Update your .env file:")
        print(f"     TRUNK_ID={outbound_trunk_id}")
        print()
        print(f"  2. Configure Vobiz inbound destination:")
        print(f"     Go to Vobiz Console → Inbound Trunks → Set inbound_destination")
        print(f"     to your LiveKit SIP URI (without the 'sip:' prefix)")
        print(f"     Find your SIP URI at: LiveKit Dashboard → Settings → Project")
        print()

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(setup_trunks())
