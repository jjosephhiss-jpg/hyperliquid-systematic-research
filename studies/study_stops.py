#!/usr/bin/env python3
"""Stop-width sweep: is -4.5% the right stop for a +3% target on Hyperliquid perps?

stud y_indicators found no indicator filter that survived an out-of-sample split, but
it did expose something bigger: the same entries scored 73.8% win in the earlier half
of history and 51.1% in the recent half. At +3% target / -4.5% stop that geometry needs
60%, so the recent regime is underwater on the raw bracket.

This sweeps the stop (target held at +3%) across both halves, so the answer is not just
"which stop scored best" but "which stop is stable across regimes". Fees at 0.09% round
trip. Ties within a bar go to the stop.
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
import indicators as ind  # noqa: E402

API = "https://api.hyperliquid.xyz/info"
G24, G24MAX, G6, G1, G1MAX = 2.0, 15.0, 0.5, 0.3, 2.0
TP_FRAC = 1.03
FEE_PCT = 0.09
HORIZON = 192
WARMUP = 210
STOPS = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.5, 6.5]


def post(p):
    req = urllib.request.Request(API, data=json.dumps(p).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=45))


def main():
    watch = hlx.WATCHLIST
    now = int(time.time() * 1000)

    sigs = []          # (coin, t, entry, [highs], [lows], [closes]) — forward windows
    coins = 0
    for coin in watch:
        try:
            rows = post({"type": "candleSnapshot", "req": {
                "coin": coin, "interval": "15m",
                "startTime": now - 52 * 86400000, "endTime": now}})
        except Exception as exc:
            print(f"  {coin:9s} fetch failed: {type(exc).__name__}: {exc}", flush=True)
            continue
        if not isinstance(rows, list) or len(rows) < WARMUP + HORIZON:
            print(f"  {coin:9s} thin history: "
                  f"{len(rows) if isinstance(rows, list) else type(rows).__name__}", flush=True)
            continue
        coins += 1
        h = np.array([float(r["h"]) for r in rows])
        l = np.array([float(r["l"]) for r in rows])
        c = np.array([float(r["c"]) for r in rows])
        n = len(c)
        i = WARMUP
        while i < n - 2:
            c24 = (c[i] / c[i - 96] - 1) * 100
            c6 = (c[i] / c[i - 24] - 1) * 100
            c1 = (c[i] / c[i - 4] - 1) * 100
            if not (G24 <= c24 <= G24MAX and c6 >= G6 and G1 <= c1 <= G1MAX):
                i += 1
                continue
            j_end = min(i + 1 + HORIZON, n)
            sigs.append((coin, rows[i]["t"], float(c[i]),
                         h[i + 1:j_end], l[i + 1:j_end], c[i + 1:j_end]))
            # advance past a nominal 4.5% stop window to keep signals non-overlapping
            i += 96
    print(f"coins {coins} | signals {len(sigs)}", flush=True)
    if not sigs:
        return 1

    ts = sorted(s[1] for s in sigs)
    mid = ts[len(ts) // 2]
    halves = {"early": [s for s in sigs if s[1] < mid],
              "recent": [s for s in sigs if s[1] >= mid]}

    def evaluate(pool, stop_pct):
        sl_frac = 1 - stop_pct / 100
        rets = []
        for _coin, _t, entry, hs, ls, cs in pool:
            tp = entry * TP_FRAC
            sl = entry * sl_frac
            res = None
            for k in range(len(hs)):
                if ls[k] <= sl:
                    res = -stop_pct
                    break
                if hs[k] >= tp:
                    res = (TP_FRAC - 1) * 100
                    break
            if res is None:
                res = (cs[-1] / entry - 1) * 100 if len(cs) else 0.0
            rets.append(res - FEE_PCT)
        a = np.array(rets)
        wins = int((a > 0).sum())
        return dict(n=len(a), win=wins / len(a) * 100 if len(a) else 0.0,
                    avg=float(a.mean()), total=float(a.sum()))

    print(f"\n{'stop':>6} | {'early n':>7}{'win%':>8}{'avg%':>8} | "
          f"{'recent n':>8}{'win%':>8}{'avg%':>8} | {'both avg%':>9}")
    print("-" * 76)
    out = {}
    for stop in STOPS:
        e = evaluate(halves["early"], stop)
        r = evaluate(halves["recent"], stop)
        both = (e["avg"] * e["n"] + r["avg"] * r["n"]) / (e["n"] + r["n"])
        print(f"{-stop:>6.1f} | {e['n']:>7}{e['win']:>7.1f}%{e['avg']:>+8.3f} | "
              f"{r['n']:>8}{r['win']:>7.1f}%{r['avg']:>+8.3f} | {both:>+9.3f}")
        out[str(stop)] = {"early": e, "recent": r, "both_avg": both}

    json.dump(out, open("study_stops.json", "w"), indent=1)
    print("\nwritten: study_stops.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
