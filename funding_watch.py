#!/usr/bin/env python3
"""Funding-capture monitor for the HL desk.

WHAT THIS IS: a scanner that tells you when a perp's funding rate is high enough
that hedging the other way would pay for itself quickly. It does NOT trade. There
is no auto-hedging here on purpose - a delta-neutral pair has a liquidation risk
on the perp leg that needs a human decision about size, and getting that wrong is
how small accounts die. This finds candidates; acting on them is yours.

THE TRADE IT IS LOOKING FOR
  Funding is paid hourly between longs and shorts. If a coin's funding is strongly
  POSITIVE, longs pay shorts, so you go SHORT the perp and hedge by holding the
  asset elsewhere (spot, or an opposite position on another venue). The two legs
  cancel each other's price risk, and the funding is the return. Negative funding
  is the mirror image.

WHY MOST HIGH RATES ARE NOT WORTH IT
  You open and close TWO legs, so there are four fills of cost before you earn
  anything. At HL's taker rate (~0.045%) that is ~0.18% round trip. A rate only
  matters if it covers that in a sensible time. The median liquid name funds at
  ~11% annualised, which needs about six days to break even - not worth the
  overnight gap risk. A spike like XMR's (0.66%/day) breaks even in ~6.5 hours and
  is a genuine candidate. So the monitor reports BREAK-EVEN HOURS, not the
  headline rate. Anything that takes more than BREAK_EVEN_MAX_H hours is noise.

CAVEATS THE OUTPUT REPEATS ON PURPOSE
  Funding flips sign without warning; the hedge must be constructible (no spot
  market means an external venue and more capital); and the perp leg can be
  liquidated if the hedge is imperfect or under-margined.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

API = "https://api.hyperliquid.xyz/info"
FILLS = 4                  # open+close, two legs
TAKER = 0.045              # % per fill, HL taker
COST = FILLS * TAKER       # % round trip for the pair
BREAK_EVEN_MAX_H = 48      # flag anything that pays back its costs within this
MIN_OI_USD = 2_000_000     # ignore thin books - you cannot hedge what you cannot exit
NOTIONAL = 54.0            # what the desk has to deploy


def info(payload, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(
                API, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=40))
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


def token():
    """Bot token and chat id come from the environment. Nothing is read off disk: the
    original read a systemd unit file, which is a fine way to leak a live token into a
    transcript, and is exactly the kind of thing this repo should not ship."""
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def tg(msg):
    t, c = token()
    if not t:
        return False
    try:
        data = json.dumps({"chat_id": c, "text": msg,
                           "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{t}/sendMessage", data=data,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20)
        return True
    except Exception:
        return False


def spot_bases():
    """Base symbols that have an HL spot market - i.e. the hedge leg is on-venue."""
    sm = info({"type": "spotMeta"}) or {}
    out = set()
    for t in sm.get("tokens", []):
        out.add(t.get("name", ""))
    return out


def scan():
    mac = info({"type": "metaAndAssetCtxs"})
    if not mac:
        return None
    meta, ctxs = mac
    spot = spot_bases()
    rows = []
    for u, c in zip(meta.get("universe", []), ctxs):
        if u.get("isDelisted"):
            continue
        name = u.get("name")
        try:
            f = float(c.get("funding") or 0)            # fraction per hour
            mark = float(c.get("markPx") or 0)
            oi = float(c.get("openInterest") or 0) * mark
        except Exception:
            continue
        if oi < MIN_OI_USD or mark <= 0:
            continue
        daily = f * 24 * 100                            # % per day
        if abs(daily) < 1e-9:
            continue
        be_h = COST / (abs(daily) / 24) if daily else 1e9
        gross_day = NOTIONAL * abs(daily) / 100
        net_day = gross_day - NOTIONAL * COST / 100
        rows.append(dict(
            coin=name, daily=daily, ann=daily * 365, be_h=be_h,
            gross=gross_day, net=net_day,
            side="SHORT perp + long hedge" if daily > 0 else "LONG perp + short hedge",
            hlnative=name in spot))
    rows.sort(key=lambda r: r["be_h"])
    return rows


def report(alert_only=False):
    rows = scan()
    if not rows:
        print("funding scan failed (API)")
        return 1
    worth = [r for r in rows if r["be_h"] <= BREAK_EVEN_MAX_H]
    lines = []
    if worth:
        lines.append(f"*Funding capture — {len(worth)} worth a look*")
        for r in worth[:5]:
            leg = "hedge on HL spot" if r["hlnative"] else "hedge needs EXTERNAL venue"
            lines.append(
                f"• *{r['coin']}* {r['daily']:+.3f}%/day ({r['ann']:+.0f}%/yr)\n"
                f"   {r['side']}\n"
                f"   pays back costs in *{r['be_h']:.1f}h* · net ~${r['net']:+.3f}/day on ${NOTIONAL:.0f}\n"
                f"   {leg}")
        lines.append("\nFunding flips without warning, and the perp leg liquidates if "
                     "the hedge is imperfect. Costs assume taker both legs.")
    else:
        best = rows[0]
        lines.append(
            f"*Funding scan: nothing worth capturing.*\n"
            f"Best is {best['coin']} at {best['daily']:+.3f}%/day — needs "
            f"{best['be_h']:.0f}h to pay back fees (threshold {BREAK_EVEN_MAX_H}h).\n"
            f"Median liquid name funds at "
            f"{sorted(abs(r['ann']) for r in rows)[len(rows)//2]:.0f}%/yr.")
    body = "\n".join(lines)
    print(body)
    if worth or not alert_only:
        tg(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(report(alert_only="--alert" in sys.argv))
