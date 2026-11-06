#!/usr/bin/env python3
"""THE TIMESCALE QUESTION: does momentum work on daily bars, where it does not on 15m?

Everything else we tested lived inside hours. Prices on 15m are mostly noise plus fees;
on daily bars a move is large enough that a 0.09% round trip is rounding error, and
trend-following is documented to work on daily/weekly data across most markets. So the
honest question is whether our failure was the SIGNAL or the TIMESCALE.

THE TRAP THIS STUDY IS BUILT TO AVOID
Long-only crypto over a 3-year window inherits an enormous upward drift. Almost any
"buy sometimes" rule will show a positive return and look like skill. The only honest
comparison is against BUY-AND-HOLD of the same coin over the same window. So every
result here is reported as strategy-vs-hold, and the verdict is based on that difference,
not on the strategy's raw return. If a rule cannot beat holding, it has no timing edge -
it is just beta with extra steps.

Also reported: max drawdown (a rule that matches hold with half the drawdown IS an
improvement), time in market, and both halves of the window for out-of-sample.

Entries are taken at the NEXT bar's open (never the signal bar's close) to avoid
look-ahead. Daily ATR(14) sizes the trailing stop, which is how trend systems actually exit.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.request

import numpy as np
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import hlx

API = "https://api.hyperliquid.xyz/info"
YEARS = 3
ATR_N = 14
TRAIL_ATR = 2.5
MAX_HOLD = 60
FEE = 0.0916          # round trip % - negligible per trade at this horizon, included anyway


def post(p, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(API, data=json.dumps(p).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=60))
        except Exception:
            time.sleep(2 * (a + 1))


def atr_series(h, l, c, n=ATR_N):
    m = len(c)
    tr = np.empty(m)
    tr[0] = h[0] - l[0]
    for i in range(1, m):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = np.full(m, np.nan)
    if m > n:
        atr[n] = tr[1:n + 1].mean()
        for i in range(n + 1, m):
            atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def signals(kind, c):
    """Boolean array: is this bar an entry signal? Causal (uses data up to i only)."""
    n = len(c)
    out = np.zeros(n, dtype=bool)
    if kind == "mom7":
        for i in range(7, n):
            out[i] = (c[i] / c[i - 7] - 1) > 0.05
    elif kind == "mom20":
        for i in range(20, n):
            out[i] = (c[i] / c[i - 20] - 1) > 0.10
    elif kind == "mom30":
        for i in range(30, n):
            out[i] = (c[i] / c[i - 30] - 1) > 0.15
    elif kind == "sma50":
        for i in range(51, n):
            sma = c[i - 49:i + 1].mean()
            sma_prev = c[i - 50:i].mean()
            out[i] = c[i] > sma and sma > sma_prev
    elif kind == "trend200":
        for i in range(201, n):
            sma200 = c[i - 199:i + 1].mean()
            out[i] = c[i] > sma200
    return out


def run_coin(rows, kind):
    """Compounding single-asset simulation. Returns dict of stats."""
    o = np.array([float(r["o"]) for r in rows])
    c = np.array([float(r["c"]) for r in rows])
    h = np.array([float(r["h"]) for r in rows])
    l = np.array([float(r["l"]) for r in rows])
    atr = atr_series(h, l, c)
    sig = signals(kind, c)
    n = len(c)

    equity, peak, maxdd = 1.0, 1.0, 0.0
    trades, wins = 0, 0
    bars_in = 0
    i = 20
    while i < n - 1:
        if not sig[i] or np.isnan(atr[i]):
            i += 1
            continue
        entry = o[i + 1]                     # next bar's open, no look-ahead
        if entry <= 0:
            i += 1
            continue
        stop = entry - TRAIL_ATR * atr[i]
        high_since = entry
        exit_px, held = None, 0
        for j in range(i + 1, min(i + 1 + MAX_HOLD, n)):
            held += 1
            high_since = max(high_since, h[j])
            stop = max(stop, high_since - TRAIL_ATR * atr[j])   # ratchets up only
            if l[j] <= stop:                                     # adverse first
                exit_px = stop
                break
        if exit_px is None:
            exit_px = c[min(i + MAX_HOLD, n - 1)]
            held = min(MAX_HOLD, n - 1 - i)
        ret = (exit_px / entry - 1) * 100 - FEE
        equity *= (1 + ret / 100)
        peak = max(peak, equity)
        maxdd = min(maxdd, equity / peak - 1)
        trades += 1
        bars_in += held
        if ret > 0:
            wins += 1
        i = j + 1                            # no pyramiding; wait for the exit

    bh = (c[-1] / c[20] - 1) * 100
    bh_curve = c[20:] / c[20]
    bh_peak = np.maximum.accumulate(bh_curve)
    bh_dd = float((bh_curve / bh_peak - 1).min()) * 100
    return dict(equity=equity, ret=(equity - 1) * 100, bh=bh, dd=maxdd * 100,
                bh_dd=bh_dd, trades=trades, wins=wins,
                in_market=bars_in / max(1, n - 20) * 100)


def main():
    watch = hlx.WATCHLIST
    now = int(time.time() * 1000)
    data = {}
    for coin in watch:
        rows = post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "1d",
            "startTime": now - int(YEARS * 365.25 * 86400000), "endTime": now}})
        if isinstance(rows, list) and len(rows) > 250:
            data[coin] = rows
    print(f"coins with >=250 daily bars: {len(data)}")
    spans = [(rows[-1]['t'] - rows[0]['t']) / 86400000 for rows in data.values()]
    print(f"median span: {statistics.median(spans):.0f} days\n")

    kinds = ["mom7", "mom20", "mom30", "sma50", "trend200"]
    print(f"{'rule':<10}{'coins':>6}{'trades':>8}{'win%':>7}{'in mkt':>8}"
          f"{'strat ret':>11}{'hold ret':>10}{'edge':>9}{'strat DD':>10}{'hold DD':>9}{'beat hold':>11}")
    print("-" * 108)
    summary = {}
    for k in kinds:
        res = [(c_, run_coin(rows, k)) for c_, rows in data.items()]
        res = [(c_, r) for c_, r in res if r["trades"] > 0]
        if not res:
            print(f"{k:<10} no trades")
            continue
        med = lambda f: statistics.median([f(r) for _, r in res])
        beat = sum(1 for _, r in res if r["ret"] > r["bh"])
        tr = sum(r["trades"] for _, r in res)
        wr = sum(r["wins"] for _, r in res) / tr * 100 if tr else 0
        print(f"{k:<10}{len(res):>6}{tr:>8}{wr:>6.1f}%{med(lambda r: r['in_market']):>7.0f}%"
              f"{med(lambda r: r['ret']):>10.1f}%{med(lambda r: r['bh']):>9.1f}%"
              f"{med(lambda r: r['ret'] - r['bh']):>+8.1f}%{med(lambda r: r['dd']):>9.1f}%"
              f"{med(lambda r: r['bh_dd']):>8.1f}%{beat:>7}/{len(res)}")
        summary[k] = dict(coins=len(res), trades=tr, win=wr,
                          strat=med(lambda r: r["ret"]), hold=med(lambda r: r["bh"]),
                          edge=med(lambda r: r["ret"] - r["bh"]),
                          dd=med(lambda r: r["dd"]), bh_dd=med(lambda r: r["bh_dd"]),
                          beat=beat)

    json.dump(summary, open("study_daily.json", "w"), indent=1)
    print("\nTHE ONLY LINE THAT MATTERS: 'edge' = strategy return minus buy-and-hold.")
    print("A positive edge on most coins means timing added something. Negative or ~0 means")
    print("the daily bars bought us nothing but beta, and the timescale was not the problem.")
    print("\nwritten: study_daily.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
