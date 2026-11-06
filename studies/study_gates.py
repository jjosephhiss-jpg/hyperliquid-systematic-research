#!/usr/bin/env python3
"""Would LOOSER entry gates help?

Everything tested so far either tightened the entry (extra filters) or changed the exit.
Loosening the thresholds is a different, and testable, hypothesis - and it is the one that
lines up with the user's own observation that the desk buys too late.

Logic: the gates require 24h >= +2%, 6h >= +0.5%, 1h in [0.3, 2.0]. In practice a coin must
already have run to clear those floors, so the entry lands late in the move. Lower floors
mean entering EARLIER in a push, i.e. less extended - which should show up as (a) a larger
share of signals where the last bar is still small, and (b) a less negative median drift
right after entry.

Variants vary the floors and the ceiling independently, and each is scored on both halves
of history with the live +3% / -4.5% geometry and 0.09% fees.
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
TP_PCT, SL_PCT, FEE_PCT = 3.0, 4.5, 0.09
HORIZON, WARMUP = 192, 210

VARIANTS = {
    "LIVE  24h>=2  6h>=0.5  1h[0.3,2]":   (2.0, 0.5, 0.30, 2.0),
    "floors half: 1.0 / 0.25":            (1.0, 0.25, 0.20, 2.0),
    "floors quarter: 0.5 / 0.1":          (0.5, 0.10, 0.10, 2.0),
    "24h floor only: 1.0":                (1.0, 0.5, 0.30, 2.0),
    "24h floor only: 0.5":                (0.5, 0.5, 0.30, 2.0),
    "raise ceiling: 1h<=3.0":             (2.0, 0.5, 0.30, 3.0),
    "no 1h ceiling":                      (2.0, 0.5, 0.30, 99.0),
    "no 1h floor (1h>=0)":                (2.0, 0.5, -99.0, 2.0),
    "parked: 24h>=0 6h>=0 1h[0,2]":       (0.0, 0.0, 0.0, 2.0),
}


def post(p, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(API, data=json.dumps(p).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=45))
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


def main():
    watch = hlx.WATCHLIST
    now = int(time.time() * 1000)

    # collect every bar's features once, plus its forward path
    pool = []
    for coin in watch:
        rows = post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "15m",
            "startTime": now - 52 * 86400000, "endTime": now}})
        if not isinstance(rows, list) or len(rows) < WARMUP + HORIZON:
            continue
        c = np.array([float(r["c"]) for r in rows])
        h = np.array([float(r["h"]) for r in rows])
        l = np.array([float(r["l"]) for r in rows])
        n = len(c)
        for i in range(WARMUP, n - 13):
            c24 = (c[i] / c[i - 96] - 1) * 100
            c6 = (c[i] / c[i - 24] - 1) * 100
            c1 = (c[i] / c[i - 4] - 1) * 100
            if not (c24 >= 0.0 and c6 >= 0.0):      # keep the pool manageable
                continue
            j = min(i + 1 + HORIZON, n)
            pool.append((rows[i]["t"], c24, c6, c1, float(c[i]),
                         (c[i] / c[i - 1] - 1) * 100,
                         h[i + 1:j], l[i + 1:j], c[i + 1:j]))
        print(f"  {coin:9s} pool {len(pool)}", flush=True)

    print(f"\nbar pool: {len(pool)}")

    def outcome(entry, hs, ls, cs):
        tp, sl = entry * (1 + TP_PCT / 100), entry * (1 - SL_PCT / 100)
        for k in range(len(hs)):
            if ls[k] <= sl:
                return -SL_PCT
            if hs[k] >= tp:
                return TP_PCT
        return (cs[-1] / entry - 1) * 100 if len(cs) else 0.0

    print(f"\n{'variant':34s} | {'ALL n':>6}{'avg%':>8}{'win%':>7} | "
          f"{'early':>8}{'recent':>8} | {'lastbar+':>8}{'drift4':>8}")
    print("-" * 100)
    results = {}
    for name, (f24, f6, f1lo, f1hi) in VARIANTS.items():
        sel = []
        for rec in pool:
            t, c24, c6, c1, entry, bak1, hs, ls, cs = rec
            if f24 <= c24 <= 15.0 and c6 >= f6 and f1lo <= c1 <= f1hi:
                sel.append((t, entry, bak1, hs, ls, cs))
        if len(sel) < 10:
            print(f"{name:34s} | {len(sel):>6}  too few signals")
            continue
        ts = sorted(s[0] for s in sel)
        mid = ts[len(ts) // 2]
        early = [s for s in sel if s[0] < mid]
        recent = [s for s in sel if s[0] >= mid]
        rets = np.array([outcome(s[1], s[3], s[4], s[5]) for s in sel]) - FEE_PCT
        e = np.array([outcome(s[1], s[3], s[4], s[5]) for s in early]) - FEE_PCT
        r = np.array([outcome(s[1], s[3], s[4], s[5]) for s in recent]) - FEE_PCT
        lastbar = np.mean([s[2] > 0 for s in sel]) * 100
        drift = np.mean([(s[5][3] / s[1] - 1) * 100 if len(s[5]) > 3 else 0 for s in sel])
        print(f"{name:34s} | {len(sel):>6}{rets.mean():>+8.3f}"
              f"{(rets>0).mean()*100:>6.1f}% | {e.mean():>+8.3f}{r.mean():>+8.3f} | "
              f"{lastbar:>7.0f}%{drift:>+8.3f}")
        results[name] = dict(n=len(sel), avg=float(rets.mean()),
                             win=float((rets > 0).mean() * 100),
                             early=float(e.mean()), recent=float(r.mean()),
                             lastbar_up=float(lastbar), drift4=float(drift))

    json.dump(results, open("study_gates.json", "w"), indent=1)
    print("\nwritten: study_gates.json")
    print("\nfees are 0.09%/trade; anything whose avg% is <= ~0.00 is not covering cost.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
