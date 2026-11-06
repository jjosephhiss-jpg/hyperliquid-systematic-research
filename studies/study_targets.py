#!/usr/bin/env python3
"""Target sweep, driven by the distribution shape found in study_entry_timing.

That study showed the post-entry return is RIGHT-SKEWED: the mean drift is positive
while the MEDIAN is negative at +1, +4 and +8 bars, and roughly half of all entries go
underwater immediately. If most trades lose a little and a minority run far, then a
small fixed +3% target truncates the only thing that pays, while the -4.5% stop takes
every loser in full.

So the open question is the opposite of the one I asked yesterday: not "is the stop too
wide", but "is the TARGET too tight". Sweeps the target with the stop fixed at -4.5%,
over both halves of history, and reports the max favourable excursion each entry
actually achieved (i.e. how much was there to be had).
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
G24, G24MAX, G6, G1, G1MAX = 2.0, 15.0, 0.5, 0.3, 2.0
SL_PCT = 4.5
FEE_PCT = 0.09
HORIZON = 192          # 48h
WARMUP = 210
TARGETS = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0]


def post(p):
    req = urllib.request.Request(API, data=json.dumps(p).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=45))


def main():
    watch = hlx.WATCHLIST
    now = int(time.time() * 1000)

    sigs = []       # (t, entry, highs[], lows[], closes[])
    for coin in watch:
        try:
            rows = post({"type": "candleSnapshot", "req": {
                "coin": coin, "interval": "15m",
                "startTime": now - 52 * 86400000, "endTime": now}})
        except Exception as exc:
            print(f"  {coin:9s} {type(exc).__name__}: {exc}", flush=True)
            continue
        if not isinstance(rows, list) or len(rows) < WARMUP + HORIZON:
            continue
        c = np.array([float(r["c"]) for r in rows])
        h = np.array([float(r["h"]) for r in rows])
        l = np.array([float(r["l"]) for r in rows])
        n = len(c)
        i = WARMUP
        while i < n - 12:
            c24 = (c[i] / c[i - 96] - 1) * 100
            c6 = (c[i] / c[i - 24] - 1) * 100
            c1 = (c[i] / c[i - 4] - 1) * 100
            if G24 <= c24 <= G24MAX and c6 >= G6 and G1 <= c1 <= G1MAX:
                j = min(i + 1 + HORIZON, n)
                sigs.append((rows[i]["t"], float(c[i]), h[i + 1:j], l[i + 1:j], c[i + 1:j]))
                i += 96
            else:
                i += 1

    print(f"signals: {len(sigs)}")
    ts = sorted(s[0] for s in sigs)
    mid = ts[len(ts) // 2]
    halves = {"early": [s for s in sigs if s[0] < mid],
              "recent": [s for s in sigs if s[0] >= mid]}

    # how much was actually on offer?
    mfe, mae = [], []
    for _t, e, hs, ls, cs in sigs:
        if len(hs):
            mfe.append((hs.max() / e - 1) * 100)
            mae.append((ls.min() / e - 1) * 100)
    mfe, mae = np.array(mfe), np.array(mae)
    print(f"\nEXCURSION over the 48h window (n={len(mfe)})")
    print(f"  max favourable: median {np.median(mfe):+.2f}%  mean {mfe.mean():+.2f}%  "
          f"p75 {np.percentile(mfe,75):+.2f}%  p90 {np.percentile(mfe,90):+.2f}%")
    print(f"  max adverse:    median {np.median(mae):+.2f}%  mean {mae.mean():+.2f}%  "
          f"p25 {np.percentile(mae,25):+.2f}%  p10 {np.percentile(mae,10):+.2f}%")
    print(f"  reached +3% before -4.5%: {np.mean([ (h>=e*1.03).any() for _t,e,h,_l,_c in sigs ])*100:.0f}% of the time (ignores ordering)")
    for tgt in (3, 5, 8, 10):
        hit = np.mean([ (h >= e*(1+tgt/100)).any() for _t, e, h, _l, _c in sigs ]) * 100
        print(f"  ever touched +{tgt}%: {hit:.0f}%")

    def evaluate(pool, tp_pct):
        rets = []
        for _t, e, hs, ls, cs in pool:
            tp, sl = e * (1 + tp_pct / 100), e * (1 - SL_PCT / 100)
            res = None
            for k in range(len(hs)):
                if ls[k] <= sl:
                    res = -SL_PCT
                    break
                if hs[k] >= tp:
                    res = tp_pct
                    break
            if res is None:
                res = (cs[-1] / e - 1) * 100 if len(cs) else 0.0
            rets.append(res - FEE_PCT)
        a = np.array(rets)
        return dict(n=len(a), avg=float(a.mean()), total=float(a.sum()),
                    pos=(a > 0).mean() * 100)

    print(f"\nTARGET SWEEP (stop fixed at -{SL_PCT}%, fees {FEE_PCT}%)")
    print(f"{'target':>7} | {'early n':>7}{'avg%':>8}{'tot%':>8} | "
          f"{'recent n':>8}{'avg%':>8}{'tot%':>8} | {'both avg%':>9}")
    print("-" * 76)
    out = {}
    for tp in TARGETS:
        e = evaluate(halves["early"], tp)
        r = evaluate(halves["recent"], tp)
        both = (e["avg"] * e["n"] + r["avg"] * r["n"]) / (e["n"] + r["n"])
        print(f"{tp:>6.1f}% | {e['n']:>7}{e['avg']:>+8.3f}{e['total']:>+8.1f} | "
              f"{r['n']:>8}{r['avg']:>+8.3f}{r['total']:>+8.1f} | {both:>+9.3f}")
        out[str(tp)] = {"early": e, "recent": r, "both": both}

    json.dump({"mfe": {"median": float(np.median(mfe)), "mean": float(mfe.mean())},
               "mae": {"median": float(np.median(mae)), "mean": float(mae.mean())},
               "targets": out},
              open("study_targets.json", "w"), indent=1)
    print("\nwritten: study_targets.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
