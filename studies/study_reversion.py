#!/usr/bin/env python3
"""MEAN REVERSION on daily bars - the last untested family, pointed at majors.

Everything tested so far was momentum: buy what has gone up. We tested buying strength and
we tested shorting strength. We never tested BUYING WEAKNESS - coins that have fallen hard,
on the bet they snap back - and the earlier universe study says the pond matters more than
the rod, so this runs on MAJORS (BTC/ETH/SOL/HYPE, real upward drift) with the full
watchlist as an unbiased control.

WHY THE EXIT IS DIFFERENT HERE. A trend system exits with a trailing stop; a mean-reversion
system exits when the mean is reached. Using a trend trail on a reversion entry would fight
the thesis and produce noise. So the exit is:
    take profit  - close back at/above the 20-day mean (the reversion has happened)
    stop         - 3 x ATR14 below entry (mean reversion failed, cut it)
    time stop    - 10 bars (if it has not reverted, the thesis is dead)
Whichever comes first, adverse-first within a bar.

THE BENCHMARK QUESTION IS DIFFERENT HERE TOO. A dip-buyer is out of the market most of the
time, so on an asset that rises 117% it will almost certainly UNDERPERFORM buy-and-hold in
raw return. That is not failure - the question is whether it earns its return with a much
smaller drawdown. So this reports return/|max drawdown| alongside raw returns, and the
verdict rests on whether it makes money in absolute terms with a better risk profile.
Cash (0%) is the floor: a rule that loses money has failed regardless of beating hold.

Entries are taken at the NEXT bar's open. No look-ahead anywhere.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.dirname(_here))
import hlx
import study_daily as SD

MAJORS = ["BTC", "ETH", "SOL", "HYPE"]
STOP_ATR = 3.0
MAX_HOLD = 10
FEE = 0.0916


def rsi(c, n):
    """Wilder RSI, causal."""
    m = len(c)
    out = np.full(m, np.nan)
    if m <= n:
        return out
    gain = np.zeros(m)
    loss = np.zeros(m)
    for i in range(1, m):
        d = c[i] - c[i - 1]
        gain[i] = max(d, 0.0)
        loss[i] = max(-d, 0.0)
    ag = gain[1:n + 1].mean()
    al = loss[1:n + 1].mean()
    for i in range(n + 1, m):
        ag = (ag * (n - 1) + gain[i]) / n
        al = (al * (n - 1) + loss[i]) / n
        if al == 0:
            out[i] = 100.0
        else:
            rs = ag / al
            out[i] = 100 - 100 / (1 + rs)
    return out


def reversion_signals(kind, c, h, l):
    n = len(c)
    out = np.zeros(n, dtype=bool)
    sma20 = np.full(n, np.nan)
    sd20 = np.full(n, np.nan)
    for i in range(19, n):
        w = c[i - 19:i + 1]
        sma20[i] = w.mean()
        sd20[i] = w.std()
    if kind == "rsi14<30":
        r = rsi(c, 14)
        out = r < 30
    elif kind == "rsi2<10":
        r = rsi(c, 2)
        out = r < 10
    elif kind == "dip3>-8":
        for i in range(3, n):
            out[i] = (c[i] / c[i - 3] - 1) < -0.08
    elif kind == "dip5>-12":
        for i in range(5, n):
            out[i] = (c[i] / c[i - 5] - 1) < -0.12
    elif kind == "bb-lower":
        for i in range(20, n):
            if not np.isnan(sma20[i]):
                out[i] = c[i] < sma20[i] - 2 * sd20[i]
    elif kind == "off-high15":
        for i in range(20, n):
            hi = c[i - 19:i + 1].max()
            out[i] = c[i] <= hi * 0.85
    return out, sma20


def run_coin(rows, kind):
    o = np.array([float(r["o"]) for r in rows])
    c = np.array([float(r["c"]) for r in rows])
    h = np.array([float(r["h"]) for r in rows])
    l = np.array([float(r["l"]) for r in rows])
    atr = SD.atr_series(h, l, c)
    sig, sma20 = reversion_signals(kind, c, h, l)
    n = len(c)

    equity, peak, maxdd = 1.0, 1.0, 0.0
    trades = wins = bars_in = 0
    i = 25
    while i < n - 1:
        if not sig[i] or np.isnan(atr[i]) or np.isnan(sma20[i]):
            i += 1
            continue
        entry = o[i + 1]
        if entry <= 0:
            i += 1
            continue
        target = sma20[i]                    # the mean it should revert to
        if target <= entry:                  # already above the mean - no trade
            i += 1
            continue
        stop = entry - STOP_ATR * atr[i]
        exit_px, j = None, i + 1
        while j < min(i + 1 + MAX_HOLD, n):
            if l[j] <= stop:                 # adverse first
                exit_px = stop
                break
            if c[j] >= target:               # reverted to the mean
                exit_px = c[j]
                break
            j += 1
        if exit_px is None:
            j = min(i + MAX_HOLD, n - 1)
            exit_px = c[j]
        ret = (exit_px / entry - 1) * 100 - FEE
        equity *= (1 + ret / 100)
        peak = max(peak, equity)
        maxdd = min(maxdd, equity / peak - 1)
        trades += 1
        bars_in += max(1, j - i)
        if ret > 0:
            wins += 1
        i = j + 1

    bh = (c[-1] / c[25] - 1) * 100
    curve = c[25:] / c[25]
    bh_dd = float((curve / np.maximum.accumulate(curve) - 1).min()) * 100
    ret = (equity - 1) * 100
    return dict(ret=ret, bh=bh, dd=maxdd * 100, bh_dd=bh_dd, trades=trades,
                wins=wins, in_market=bars_in / max(1, n - 25) * 100)


def main():
    watch = hlx.WATCHLIST
    coins = sorted(set(watch) | set(MAJORS))
    now = int(time.time() * 1000)
    data = {}
    for c in coins:
        rows = SD.post({"type": "candleSnapshot", "req": {
            "coin": c, "interval": "1d",
            "startTime": now - int(SD.YEARS * 365.25 * 86400000), "endTime": now}})
        if isinstance(rows, list) and len(rows) > 250:
            data[c] = rows
    print(f"loaded {len(data)} coins, 3y daily\n")

    universes = {
        "MAJORS": [c for c in MAJORS if c in data],
        "FULL": list(data),
    }
    kinds = ["rsi14<30", "rsi2<10", "dip3>-8", "dip5>-12", "bb-lower", "off-high15"]

    hdr = (f"{'univ':<8}{'rule':<12}{'coins':>6}{'trd':>5}{'win%':>6}{'inmkt':>7}"
           f"{'strat':>9}{'hold':>8}{'sDD':>8}{'hDD':>8}{'ret/DD':>8}{'beat':>7}")
    print(hdr); print("-" * len(hdr))
    out = {}
    for uname, cs in universes.items():
        rows = {c: data[c] for c in cs}
        for k in kinds:
            res = [(c, run_coin(r, k)) for c, r in rows.items()]
            res = [(c, r) for c, r in res if r["trades"] > 0]
            if not res:
                print(f"{uname:<8}{k:<12} no trades")
                continue
            med = lambda f: statistics.median([f(r) for _, r in res])
            tr = sum(r["trades"] for _, r in res)
            wr = sum(r["wins"] for _, r in res) / tr * 100 if tr else 0
            beat = sum(1 for _, r in res if r["ret"] > r["bh"])
            s_ret, s_dd = med(lambda r: r["ret"]), med(lambda r: r["dd"])
            ratio = s_ret / abs(s_dd) if s_dd else 0
            print(f"{uname:<8}{k:<12}{len(res):>6}{tr:>5}{wr:>5.0f}%"
                  f"{med(lambda r: r['in_market']):>6.0f}%{s_ret:>8.0f}%"
                  f"{med(lambda r: r['bh']):>7.0f}%{s_dd:>7.0f}%"
                  f"{med(lambda r: r['bh_dd']):>7.0f}%{ratio:>8.2f}{beat:>5}/{len(res)}")
            out[f"{uname}|{k}"] = dict(coins=len(res), trades=tr, win=wr, strat=s_ret,
                                       hold=med(lambda r: r["bh"]), dd=s_dd,
                                       bh_dd=med(lambda r: r["bh_dd"]), ratio=ratio, beat=beat)
        print()

    json.dump(out, open("study_reversion.json", "w"), indent=1)

    print("=" * 78)
    good = {k: v for k, v in out.items() if v["strat"] > 0}
    if good:
        print("PROFITABLE IN ABSOLUTE TERMS (cash is 0%, so this is the bar):")
        for k, v in sorted(good.items(), key=lambda kv: -kv[1]["ratio"]):
            print(f"  {k:<22} {v['strat']:+.0f}%  DD {v['dd']:.0f}%  "
                  f"ret/DD {v['ratio']:.2f}  (hold {v['hold']:+.0f}%, hold DD {v['bh_dd']:.0f}%)")
    else:
        print("NOTHING was profitable in absolute terms. Mean reversion on this data loses money.")
        print("Best by return/DD ratio:")
        for k, v in sorted(out.items(), key=lambda kv: -kv[1]["ratio"])[:5]:
            print(f"  {k:<22} {v['strat']:+.0f}%  DD {v['dd']:.0f}%  ret/DD {v['ratio']:.2f}")
    print("\nwritten: study_reversion.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
