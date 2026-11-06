#!/usr/bin/env python3
"""Out-of-sample check for the indicator filters found in study_indicators.py.

The first study tested twelve variants on all the data and two of them looked good.
Twelve tries will always produce a couple of winners by luck, so the honest question
is: does the filter still work on data it was NOT chosen from?

Splits each coin's signals at its own median timestamp: the earlier half is the
"in-sample" period (what the filter was chosen from), the later half is held out.
A filter that only works in the first half is noise. Report both.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.dirname(_here))
import hlx
from study_indicators import study_coin, FEE_PCT, TP_FRAC, SL_FRAC  # noqa: E402

PICKS = ["baseline (momentum only)", "+ RSI < 60", "+ stoch %K < 80",
         "+ %B < 1.0 (not above band)", "TIGHT: RSI<60 & EMA50>EMA200 & MACD>0"]


def stats(sel):
    if not sel:
        return None
    w = sum(1 for t in sel if t["result"] == 1)
    l = sum(1 for t in sel if t["result"] == -1)
    to = [t for t in sel if t["result"] == 0]
    rets = []
    for t in sel:
        if t["result"] == 1:
            rets.append((TP_FRAC - 1) * 100)
        elif t["result"] == -1:
            rets.append((SL_FRAC - 1) * 100)
        else:
            rets.append(t["ret"])
    net = np.array(rets) - FEE_PCT
    return dict(n=len(sel), w=w, l=l, to=len(to),
                wr=(w / (w + l) * 100) if (w + l) else float("nan"),
                avg=float(net.mean()), total=float(net.sum()))


def main():
    import re
    watch = hlx.WATCHLIST

    first, second = [], []
    coins = 0
    for coin in watch:
        try:
            res = study_coin(coin)
        except Exception:
            continue
        if not res:
            continue
        coins += 1
        _, trades = res
        if len(trades) < 4:
            first.extend(trades)
            continue
        ts = sorted(t["t"] for t in trades)
        mid = ts[len(ts) // 2]
        first.extend(t for t in trades if t["t"] < mid)
        second.extend(t for t in trades if t["t"] >= mid)

    print(f"coins: {coins} | in-sample signals: {len(first)} | held-out signals: {len(second)}\n")
    hdr = f"{'variant':38s} | {'IN-SAMPLE':>28} | {'HELD OUT':>28}"
    print(hdr)
    print(f"{'':38s} | {'n':>5}{'win%':>8}{'avg%':>8}{'tot%':>7} | "
          f"{'n':>5}{'win%':>8}{'avg%':>8}{'tot%':>7}")
    print("-" * len(hdr))

    out = {}
    for name in PICKS:
        a = stats([t for t in first if t["flags"][name]])
        b = stats([t for t in second if t["flags"][name]])
        fa = f"{a['n']:>5}{a['wr']:>7.1f}%{a['avg']:>+8.3f}{a['total']:>+7.1f}" if a else f"{0:>5}"
        fb = f"{b['n']:>5}{b['wr']:>7.1f}%{b['avg']:>+8.3f}{b['total']:>+7.1f}" if b else f"{0:>5}"
        print(f"{name:38s} | {fa} | {fb}")
        out[name] = {"in": a, "out": b}

    json.dump(out, open("study_oos.json", "w"), indent=1)
    print("\nwritten: study_oos.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
