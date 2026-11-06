#!/usr/bin/env python3
"""Model the profit-ratchet, so the trailing-stop change rests on numbers.

The live ratchet arms once a position is +RATCHET_START% up, then holds a stop
RATCHET_TRAIL% below the peak. On the 2026-11-05 ZEC trade that armed at +1% and
trailed 0.7%, so a ~+1.5% peak banked only +0.8% (9.7c of a possible ~20c). The
user spotted the giveback; this measures whether tightening actually helps.

Simulates, bar by bar, with the current +2% target and -4.5% stop:
  arm at S%, stop = max(breakeven, peak - T%) once peak >= S%
compares that against a plain fixed stop (no ratchet) and against the old settings.

Conservative: within a bar the adverse move is applied before the favourable one, so
a bar that could have stopped us out does.
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
TP_PCT, SL_PCT, FEE_PCT = 2.0, 4.5, 0.09
HORIZON, WARMUP = 192, 210

CONFIGS = {
    "no ratchet (fixed stop)":        None,
    "LIVE arm+1.0 / trail 0.7":       (1.0, 0.7),
    "proposed arm+0.75 / trail 0.4":  (0.75, 0.4),
    "tighter arm+0.5 / trail 0.3":    (0.5, 0.3),
    "loose arm+1.5 / trail 1.0":      (1.5, 1.0),
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


def simulate(entry, hs, ls, cfg):
    """Return the % result of one trade under a given ratchet configuration."""
    tp = entry * (1 + TP_PCT / 100)
    sl = entry * (1 - SL_PCT / 100)
    arm, trail = (cfg if cfg else (None, None))
    stop = sl
    peak = 0.0                      # best favourable % seen so far
    for k in range(len(hs)):
        # adverse first: if the stop is hit inside this bar, we are out
        if ls[k] <= stop:
            return (stop / entry - 1) * 100
        if hs[k] >= tp:
            return TP_PCT
        # then update the peak and ratchet the stop up
        hi_pct = (hs[k] / entry - 1) * 100
        if hi_pct > peak:
            peak = hi_pct
        if cfg and peak >= arm:
            new_stop = entry * (1 + max(0.0, peak - trail) / 100)
            if new_stop > stop:
                stop = new_stop
    return (ls[-1] / entry - 1) * 100 if len(ls) else 0.0


def main():
    watch = hlx.WATCHLIST
    now = int(time.time() * 1000)

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
    ts = sorted(s[0] for s in sigs)
    mid = ts[len(ts) // 2]
    early = [s for s in sigs if s[0] < mid]
    recent = [s for s in sigs if s[0] >= mid]

    print(f"\nTP +{TP_PCT}% / SL -{SL_PCT}% / fees {FEE_PCT}%")
    print(f"{'ratchet config':32s} | {'ALL avg%':>9}{'win%':>7} | "
          f"{'early':>8}{'recent':>8} | {'giveback%':>10}")
    print("-" * 88)
    out = {}
    for name, cfg in CONFIGS.items():
        def run(pool):
            return np.array([simulate(e, hs, ls, cfg) for _t, e, hs, ls in pool]) - FEE_PCT
        allr, er, rr_ = run(sigs), run(early), run(recent)
        # how much of the peak does this config keep, versus the theoretical max?
        keep = []
        for _t, e, hs, ls in sigs:
            if len(hs):
                peak_pct = (hs.max() / e - 1) * 100
                if peak_pct > 0.5:
                    keep.append(1 - max(0.0, (peak_pct - simulate(e, hs, ls, cfg) - FEE_PCT)) / peak_pct)
        giveback = (1 - float(np.mean(keep))) * 100 if keep else float("nan")
        print(f"{name:32s} | {allr.mean():>+9.3f}{(allr>0).mean()*100:>6.1f}% | "
              f"{er.mean():>+8.3f}{rr_.mean():>+8.3f} | {giveback:>9.0f}%")
        out[name] = dict(all=float(allr.mean()), early=float(er.mean()),
                         recent=float(rr_.mean()),
                         win=float((allr > 0).mean() * 100), giveback=giveback)

    json.dump(out, open("study_ratchet.json", "w"), indent=1)
    print("\nwritten: study_ratchet.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
