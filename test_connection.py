"""
test_connection.py
Run this to verify Tastytrade connection, session, and live data.
Safe to run any time — read-only, no trades placed.

Usage:
    python test_connection.py
"""

import asyncio
from dotenv import load_dotenv
load_dotenv()

from data.tastytrade import get_session, get_quote
from data.market import get_vix, get_ivr, classify_vix


async def test_session():
    print("\n── 1. Session ─────────────────────────────")
    try:
        session = await get_session()
        print(f"✅ Logged in successfully")
        print(f"   Session type: {type(session).__name__}")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return False
    return True


async def test_quote():
    print("\n── 2. Live quote (SPY) ────────────────────")
    try:
        quote = await get_quote("SPY")
        print(f"✅ SPY quote received")
        print(f"   Bid: ${quote['bid']:.2f}")
        print(f"   Ask: ${quote['ask']:.2f}")
        print(f"   Mid: ${quote['mid']:.2f}")
    except Exception as e:
        print(f"❌ Quote fetch failed: {e}")
        print(f"   Note: This may fail outside market hours — that is normal")


async def test_vix():
    print("\n── 3. VIX level ───────────────────────────")
    try:
        vix    = await get_vix()
        regime = classify_vix(vix)
        print(f"✅ VIX: {vix:.1f} — {regime.upper()}")
    except Exception as e:
        print(f"❌ VIX fetch failed: {e}")
        print(f"   Note: May fail outside market hours — that is normal")


async def test_ivr():
    print("\n── 4. IVR (AAPL) ──────────────────────────")
    try:
        ivr = await get_ivr("AAPL")
        print(f"✅ AAPL IVR: {ivr:.1f}")
    except Exception as e:
        print(f"❌ IVR fetch failed: {e}")
        print(f"   Note: May fail outside market hours — that is normal")


async def test_option_chain():
    print("\n── 5. Option chain (AAPL) ─────────────────")
    try:
        from data.tastytrade import get_option_chain_strikes
        from signals.chain import select_expiry
        from config.thresholds import DTE_CREDIT_MIN, DTE_CREDIT_MAX

        expiry = await select_expiry("AAPL", DTE_CREDIT_MIN, DTE_CREDIT_MAX)
        if expiry:
            print(f"✅ Best expiry found: {expiry}")
            strikes = await get_option_chain_strikes("AAPL", expiry)
            if strikes:
                print(f"   {len(strikes)} strikes available")
                mid = strikes[len(strikes) // 2]
                print(f"   Sample strike: ${mid['strike']:.0f}")
            else:
                print(f"   No strikes returned for {expiry}")
        else:
            print(f"   No expiry found in {DTE_CREDIT_MIN}–{DTE_CREDIT_MAX} DTE range")
            print(f"   Note: Normal if markets are closed or no weeklies listed")
    except Exception as e:
        print(f"❌ Option chain failed: {e}")
        print(f"   Note: May fail outside market hours — that is normal")


async def main():
    print("=" * 50)
    print("  Tastytrade Connection Test")
    print("=" * 50)

    ok = await test_session()
    if not ok:
        print("\n❌ Session failed — stopping here.")
        print("   Check TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD in your .env file")
        return

    await test_quote()
    await test_vix()
    await test_ivr()
    await test_option_chain()

    print("\n" + "=" * 50)
    print("  Test complete")
    print("  Tests 2–5 may show errors outside market hours.")
    print("  As long as Test 1 (session) passes, your")
    print("  credentials and connection are working.")
    print("=" * 50)


asyncio.run(main())
