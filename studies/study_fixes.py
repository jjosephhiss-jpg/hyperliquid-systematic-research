#!/usr/bin/env python3
"""The two candidate fixes, tested rather than asserted.

The diagnosis so far: momentum-bracket entries on 15m memecoin candles are a coin flip
in magnitude (median favourable excursion +4.29% vs median adverse -4.34%), no entry
filter repairs it, and the held-out half is negative at every stop and target width.

Two hypotheses follow directly from that, and both are testable:

  TEST 1 - FLIP THE SIDE. If buying a push is a coin flip whose median trade goes
  underwater immediately, the mirror trade may be the positive side. Same signals,
  same geometry, entered SHORT (TP -3%, SL +4.5%). Mirror-image logic, so if the long
  side were truly neutral this should also be neutral; if the long side is quietly
  negative, this should be quietly positive.

  TEST 2 - CHANGE THE MARKET AND THE CLOCK. Momentum is documented to be more reliable
  on liquid instruments and longer horizons, where trend persists and noise+spread are
  a smaller share of the move. Same gates, same +3%/-4.5% geometry, applied to BTC, ETH,
  SOL and HYPE on 1h and 4h candles instead of illiquid memecoins on 15m.

Both report in-sample and held-out halves.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.dirname(_here))
import hlx

API = "https://api.hyperliquid.xyz/info"
TP_PCT, SL_PCT = 3.0, 4.5
FEE_PCT = 0.09
HORIZON = 48           # bars to wait (scaled per timeframe so it's ~48h)


def post(p, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(API, data=json.dumps(p).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=45))
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


def bars_per(interval):
    return {"15m": 4, "1h": 1, "4h": 1}[interval]


def gates(cl, i, interval):
    """Momentum gates scaled to the candle size: 24h / 6h / 1h windows."""
    per_hour = {"15m": 4, "1h": 1, "4h": 0.25}[interval]
    a24, a6, a1 = int(24 * per_hour), int(6 * per_hour), int(1 * per_hour)
    if i - a24 < 0 or a6 < 1 or a1 < 1:
        return None
    c24 = (cl[i] / cl[i - a24] - 1) * 100
    c6 = (cl[i] / cl[i - a6] - 1) * 100
    c1 = (cl[i] / cl[i - a1] - 1) * 100
    return (c24, c6, c1)


def walk_long(entry, hs, ls, cs):
    tp, sl = entry * (1 + TP_PCT / 100), entry * (1 - SL_PCT / 100)
    for k in range(len(hs)):
        if ls[k] <= sl:
            return -SL_PCT
        if hs[k] >= tp:
            return TP_PCT
    return (cs[-1] / entry - 1) * 100 if len(cs) else 0.0


def walk_short(entry, hs, ls, cs):
    tp, sl = entry * (1 - TP_PCT / 100), entry * (1 + SL_PCT / 100)
    for k in range(len(hs)):
        if hs[k] >= sl:
            return -SL_PCT          # adverse first: stop wins ties
        if ls[k] <= tp:
            return TP_PCT
    return (entry / cs[-1] - 1) * 100 if len(cs) else 0.0


def stats(rets):
    a = np.array(rets) - FEE_PCT
    if not len(a):
        return None
    return dict(n=len(a), avg=float(a.mean()), total=float(a.sum()),
                pos=float((a > 0).mean() * 100))


def run(coins, interval, warmup, label, results):
    now = int(time.time() * 1000)
    days = 52 if interval == "15m" else (120 if interval == "1h" else 365)
    sig = []
    for coin in coins:
        rows = post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval,
            "startTime": now - days * 86400000, "endTime": now}})
        if not isinstance(rows, list) or len(rows) < warmup + HORIZON:
            continue
        c = np.array([float(r["c"]) for r in rows])
        h = np.array([float(r["h"]) for r in rows])
        l = np.array([float(r["l"]) for r in rows])
        n = len(c)
        i = warmup
        step = 96 if interval == "15m" else (24 if interval == "1h" else 6)
        while i < n - HORIZON:
            g = gates(c, i, interval)
            if g and 2.0 <= g[0] <= 15.0 and g[1] >= 0.5 and 0.3 <= g[2] <= 2.0:
                j = min(i + 1 + HORIZON, n)
                sig.append((rows[i]["t"], float(c[i]), h[i + 1:j], l[i + 1:j], c[i + 1:j]))
                i += step
            else:
                i += 1
    if not sig:
        print(f"{label}: no signals")
        return
    ts = sorted(s[0] for s in sig)
    mid = ts[len(ts) // 2]
    early = [s for s in sig if s[0] < mid]
    recent = [s for s in sig if s[0] >= mid]
    print(f"\n{label}  (n={len(sig)})")
    for name, fn in (("LONG (as traded)", walk_long), ("SHORT (fade the pump)", walk_short)):
        e = stats([fn(*s[1:]) for s in early])
        r = stats([fn(*s[1:]) for s in recent])
        both = stats([fn(*s[1:]) for s in sig])
        print(f"   {name:24s} early {e['avg']:+.3f}%/trade ({e['n']})  "
              f"recent {r['avg']:+.3f}%/trade ({r['n']})  "
              f"ALL {both['avg']:+.3f}%  positive-trades {both['pos']:.0f}%")
        results[f"{label}|{name}"] = {"early": e, "recent": r, "all": both}


def main():
    watch = hlx.WATCHLIST
    results = {}

    print("=" * 78)
    print("TEST 1 - the same 30 memecoins and 15m clock, long versus short")
    print("=" * 78)
    run(watch, "15m", 210, "memecoins 15m", results)

    majors = [c for c in ("BTC", "ETH", "SOL", "HYPE", "ZEC") if c in
              [u for u in watch] or True]
    print()
    print("=" * 78)
    print("TEST 2 - liquid majors, longer clocks")
    print("=" * 78)
    for interval, warm in (("1h", 300), ("4h", 300)):
        run(majors, interval, warm, f"majors {interval}", results)

    json.dump(results, open("study_fixes.json", "w"), indent=1)
    print("\nwritten: study_fixes.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
