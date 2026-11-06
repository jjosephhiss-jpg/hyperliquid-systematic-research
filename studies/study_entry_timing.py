#!/usr/bin/env python3
"""Does the desk buy the top? Testing the user's observation directly.

Hypothesis (user, 2026-11-05): "some of the trades are entering at the peak of the
pump, right as the momentum drops - it buys."

The gates are all TRAILING measurements (24h >= +2%, 6h >= +0.5%, 1h in [0.3, 2.0]),
checked once per cycle. By construction a coin must already have moved for a signal
to exist. So the question is empirical: what happens in the BARS IMMEDIATELY AFTER a
signal is taken?

Measures, for every historical signal:
  A. post-entry drift  - mean return over the next 1, 2, 4, 8 bars (15m each)
  B. immediate damage  - share of signals negative after 1 bar / 4 bars
  C. bar structure     - the last 1 and 2 completed 15m bars at signal time, and
                         whether the signal bar itself was up or down
  D. filter variants   - does requiring recent bar strength, or rejecting
                         deceleration, improve the +3%/-4.5% bracket outcome?
     (each reported in-sample and out-of-sample, as study_oos does)

Causal throughout: only bars at or before the signal index feed the signal; forward
bars only feed the outcome.
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
TP_FRAC, SL_FRAC = 1.03, 0.955
FEE_PCT = 0.09
HORIZON = 192
WARMUP = 210


def post(p):
    req = urllib.request.Request(API, data=json.dumps(p).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=45))


def collect(coin, now):
    rows = post({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "15m",
        "startTime": now - 52 * 86400000, "endTime": now}})
    if not isinstance(rows, list) or len(rows) < WARMUP + HORIZON:
        return []
    c = np.array([float(r["c"]) for r in rows])
    h = np.array([float(r["h"]) for r in rows])
    l = np.array([float(r["l"]) for r in rows])
    n = len(c)
    out = []
    i = WARMUP
    while i < n - 12:
        c24 = (c[i] / c[i - 96] - 1) * 100
        c6 = (c[i] / c[i - 24] - 1) * 100
        c1 = (c[i] / c[i - 4] - 1) * 100
        if not (G24 <= c24 <= G24MAX and c6 >= G6 and G1 <= c1 <= G1MAX):
            i += 1
            continue

        entry = float(c[i])
        bak1 = (c[i] / c[i - 1] - 1) * 100          # last completed bar
        bak2 = (c[i - 1] / c[i - 2] - 1) * 100      # the one before it
        # trailing 1h split in half: are the last two 30m halves still rising?
        h1a = (c[i] / c[i - 2] - 1) * 100           # most recent 30m
        h1b = (c[i - 2] / c[i - 4] - 1) * 100       # the 30m before that

        fwd = {}
        for k in (1, 2, 4, 8):
            j = min(i + k, n - 1)
            fwd[k] = (c[j] / entry - 1) * 100

        # bracket outcome
        res, bars = None, None
        for j in range(i + 1, min(i + 1 + HORIZON, n)):
            if l[j] <= entry * SL_FRAC:
                res, bars = -1, j - i
                break
            if h[j] >= entry * TP_FRAC:
                res, bars = 1, j - i
                break
        if res is None:
            j = min(i + HORIZON, n - 1)
            fwd["timeout_ret"] = (c[j] / entry - 1) * 100
            res, bars = 0, j - i

        out.append(dict(t=rows[i]["t"], entry=entry, c24=c24, c6=c6, c1=c1,
                        bak1=bak1, bak2=bak2, h1a=h1a, h1b=h1b,
                        fwd=fwd, res=res, bars=bars))
        i += 96          # non-overlapping
    return out


FILTERS = {
    "baseline (live gates)":        lambda s: True,
    "last 15m bar was UP":          lambda s: s["bak1"] > 0,
    "last 15m bar was DOWN":        lambda s: s["bak1"] < 0,
    "1h ACCELERATING (h1a>h1b)":    lambda s: s["h1a"] > s["h1b"],
    "1h DECELERATING (h1a<h1b)":    lambda s: s["h1a"] < s["h1b"],
    "both bars up (bak1,bak2 > 0)": lambda s: s["bak1"] > 0 and s["bak2"] > 0,
    "not over-extended on 30m":     lambda s: s["h1a"] < 1.0,
    "1h <= 1.2 (damped)":           lambda s: s["c1"] <= 1.2,
}


def bracket_stats(sel):
    if not sel:
        return None
    w = sum(1 for s in sel if s["res"] == 1)
    lo = sum(1 for s in sel if s["res"] == -1)
    rets = []
    for s in sel:
        if s["res"] == 1:
            rets.append((TP_FRAC - 1) * 100)
        elif s["res"] == -1:
            rets.append((SL_FRAC - 1) * 100)
        else:
            rets.append(s["fwd"].get("timeout_ret", 0.0))
    a = np.array(rets) - FEE_PCT
    return dict(n=len(sel), w=w, l=lo,
                wr=(w / (w + lo) * 100) if (w + lo) else float("nan"),
                avg=float(a.mean()), total=float(a.sum()))


def main():
    watch = hlx.WATCHLIST
    now = int(time.time() * 1000)

    sigs = []
    for coin in watch:
        try:
            got = collect(coin, now)
        except Exception as exc:
            print(f"  {coin:9s} {type(exc).__name__}: {exc}", flush=True)
            continue
        if got:
            print(f"  {coin:9s} {len(got):3d} signals", flush=True)
        sigs.extend(got)
    print(f"\nsignals: {len(sigs)}")

    # ---- A/B: what happens straight after entry? ----
    print("\nA. POST-ENTRY DRIFT (mean % move from entry)")
    for k in (1, 2, 4, 8):
        arr = np.array([s["fwd"][k] for s in sigs])
        neg = (arr < 0).mean() * 100
        print(f"   +{k:>2} bars ({(k*15):>3}min): mean {arr.mean():+.3f}%  "
              f"median {np.median(arr):+.3f}%  negative in {neg:.0f}% of cases")

    print("\nC. BAR STRUCTURE AT SIGNAL TIME")
    for key, label in (("bak1", "last 15m bar"), ("bak2", "the 15m bar before"),
                       ("h1a", "most recent 30m"), ("h1b", "the 30m before that")):
        arr = np.array([s[key] for s in sigs])
        print(f"   {label:24s} mean {arr.mean():+.3f}%  "
              f"negative {(arr<0).mean()*100:.0f}% of signals")

    # ---- in / out split ----
    ts = sorted(s["t"] for s in sigs)
    mid = ts[len(ts) // 2]
    ins = [s for s in sigs if s["t"] < mid]
    oos = [s for s in sigs if s["t"] >= mid]

    print(f"\nD. FILTER VARIANTS  (in-sample n={len(ins)} | held-out n={len(oos)})")
    print(f"{'filter':32s} | {'IN n':>5}{'win%':>7}{'avg%':>8} | "
          f"{'OUT n':>6}{'win%':>7}{'avg%':>8}")
    print("-" * 78)
    rows = {}
    for name, fn in FILTERS.items():
        a = bracket_stats([s for s in ins if fn(s)])
        b = bracket_stats([s for s in oos if fn(s)])
        fa = f"{a['n']:>5}{a['wr']:>6.1f}%{a['avg']:>+8.3f}" if a else f"{0:>5}"
        fb = f"{b['n']:>6}{b['wr']:>6.1f}%{b['avg']:>+8.3f}" if b else f"{0:>6}"
        flag = ""
        if a and b and a["avg"] > 0 and b["avg"] > 0:
            flag = "  <== positive both halves"
        print(f"{name:32s} | {fa} | {fb}{flag}")
        rows[name] = {"in": a, "out": b}

    json.dump({"n": len(sigs), "variants": rows,
               "drift": {str(k): float(np.mean([s["fwd"][k] for s in sigs]))
                         for k in (1, 2, 4, 8)}},
              open("study_entry_timing.json", "w"), indent=1)
    print("\nwritten: study_entry_timing.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
