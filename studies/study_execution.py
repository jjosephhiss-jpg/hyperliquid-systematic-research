#!/usr/bin/env python3
"""Execution test: what is the fee model worth, and does a BTC regime filter help?

Two questions, four combinations, both halves of history:

  1. FEES. The desk pays taker (~0.0458%/side, 0.0916% round trip) because entries and
     exits cross the spread. Maker orders are roughly a third of that (0.03% round trip).
     Same trades, different toll booth - how much does it change the expectancy?

  2. REGIME FILTER. On 2026-11-06 five long positions were taken out together in a 5-9%
     market-wide selloff. Would refusing new longs while BTC itself is falling have kept
     us out - and does that hold up out-of-sample, or does it just skip good trades?

Full live config modelled: gates 24h>=2 / 6h>=0.5 / no 1h floor / 1h<=2,
TP +2%, SL -4.5%, ratchet arm +0.75% trail 0.4%, dead-trade cut at -2% once 6h old.
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
TP_PCT, SL_PCT = 2.0, 4.5
ARM, TRAIL = 0.75, 0.4
DEAD_H, DEAD_PCT = 6.0, -2.0
HORIZON, WARMUP = 192, 210
FEE_TAKER, FEE_MAKER = 0.0916, 0.03     # round trip


def post(p, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(API, data=json.dumps(p).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=45))
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


def simulate(entry, hs, ls, ts):
    """Full exit logic. Returns % result before fees."""
    tp = entry * (1 + TP_PCT / 100)
    sl = entry * (1 - SL_PCT / 100)
    stop = sl
    peak = 0.0
    for k in range(len(hs)):
        bars_held = k + 1
        # dead-trade rule: 6h+ old and worse than -2% -> cut at that bar's close
        if bars_held >= DEAD_H * 4:
            mark = (ls[k] / entry - 1) * 100
            if mark <= DEAD_PCT:
                return max(mark, (stop / entry - 1) * 100) if stop > sl else mark
        if ls[k] <= stop:
            return (stop / entry - 1) * 100
        if hs[k] >= tp:
            return TP_PCT
        hi = (hs[k] / entry - 1) * 100
        if hi > peak:
            peak = hi
        if peak >= ARM:
            new_stop = entry * (1 + max(0.0, peak - TRAIL) / 100)
            if new_stop > stop:
                stop = new_stop
    return (ls[-1] / entry - 1) * 100 if len(ls) else 0.0


def main():
    watch = hlx.WATCHLIST
    now = int(time.time() * 1000)

    # BTC 15m closes, keyed by bar time, for the regime filter
    btc = post({"type": "candleSnapshot", "req": {
        "coin": "BTC", "interval": "15m",
        "startTime": now - 52 * 86400000, "endTime": now}})
    btc_close = {c["t"]: float(c["c"]) for c in (btc or [])}
    btc_times = sorted(btc_close)

    def btc_change_6h(ts):
        """BTC's own 6h change as of bar ts (causal)."""
        if ts not in btc_close:
            prior = [t for t in btc_times if t <= ts]
            if not prior:
                return None
            ts = prior[-1]
        idx = btc_times.index(ts)
        if idx < 24:
            return None
        return (btc_close[ts] / btc_close[btc_times[idx - 24]] - 1) * 100

    sigs = []
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
        i = WARMUP
        while i < n - 13:
            c24 = (c[i] / c[i - 96] - 1) * 100
            c6 = (c[i] / c[i - 24] - 1) * 100
            c1 = (c[i] / c[i - 4] - 1) * 100
            if 2.0 <= c24 <= 15.0 and c6 >= 0.5 and -100 <= c1 <= 2.0:
                j = min(i + 1 + HORIZON, n)
                sigs.append((rows[i]["t"], float(c[i]), h[i + 1:j], l[i + 1:j]))
                i += 96
            else:
                i += 1
    print(f"signals: {len(sigs)}")
    if not sigs:
        return 1

    # pre-compute result and the BTC regime flag per signal
    recs = []
    for ts, e, hs, ls in sigs:
        recs.append((ts, simulate(e, hs, ls, None), btc_change_6h(ts)))
    unknown = sum(1 for r in recs if r[2] is None)
    print(f"BTC filter known for {len(recs)-unknown}/{len(recs)} signals")

    ts_all = sorted(r[0] for r in recs)
    mid = ts_all[len(ts_all) // 2]
    early = [r for r in recs if r[0] < mid]
    recent = [r for r in recs if r[0] >= mid]

    def stat(pool, fee, regime):
        xs = []
        for _t, res, btc6 in pool:
            if regime and btc6 is not None and btc6 < 0:
                continue          # BTC falling: stand aside
            xs.append(res - fee)
        if not xs:
            return None
        a = np.array(xs)
        return dict(n=len(a), avg=float(a.mean()), win=float((a > 0).mean() * 100))

    print(f"\n{'configuration':44s} | {'ALL n':>6}{'avg%':>8}{'win%':>7} | "
          f"{'early':>8}{'recent':>8}")
    print("-" * 92)
    out = {}
    for fee, feename in ((FEE_TAKER, "taker 0.092%"), (FEE_MAKER, "maker 0.030%")):
        for regime, rname in ((False, "no regime filter"), (True, "BTC 6h>=0 filter")):
            a = stat(recs, fee, regime)
            e = stat(early, fee, regime)
            r = stat(recent, fee, regime)
            label = f"{feename} + {rname}"
            if not a:
                print(f"{label:44s} | no signals")
                continue
            print(f"{label:44s} | {a['n']:>6}{a['avg']:>+8.3f}{a['win']:>6.1f}% | "
                  f"{e['avg']:>+8.3f}{r['avg']:>+8.3f}")
            out[label] = dict(n=a["n"], avg=a["avg"], win=a["win"],
                              early=e["avg"], recent=r["avg"])

    json.dump(out, open("study_execution.json", "w"), indent=1)
    print("\nwritten: study_execution.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
