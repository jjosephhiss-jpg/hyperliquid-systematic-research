#!/usr/bin/env python3
"""THE GAP: positive-drift assets on DAILY bars.

What the earlier studies actually covered:
  study_fixes  -> majors (BTC/ETH/SOL/HYPE) on 1h and 4h   [intraday]
  study_daily  -> the memecoin watchlist on daily bars     [decaying assets]
Nobody tested MAJORS ON DAILY. That is the one cell where trend-following is
documented to work, and it is the cell we have been missing.

Why it matters: over 3 years the watchlist's median coin returned -41% (cash beat it)
while BTC returned +117% and SOL +112%. Momentum on a decaying asset means buying each
pump before each dump - which is precisely the -86% that study_daily recorded. The
strategy may not have been the problem; the pond was.

So this runs the same five rules, at several trailing-stop widths, across three universes:
  A. MAJORS      - BTC, ETH, SOL, HYPE (positive drift, deep liquidity)
  B. WINNERS     - the watchlist coins with a positive 3-year return
  C. FULL        - the whole watchlist, as the control
Every result is judged against BUY-AND-HOLD of the same coins, because a long-only rule
on a rising asset inherits the drift and would otherwise look like skill.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.dirname(_here))
import hlx
import study_daily as SD

MAJORS = ["BTC", "ETH", "SOL", "HYPE"]


def load(coins):
    now = int(time.time() * 1000)
    out = {}
    for c in coins:
        rows = SD.post({"type": "candleSnapshot", "req": {
            "coin": c, "interval": "1d",
            "startTime": now - int(SD.YEARS * 365.25 * 86400000), "endTime": now}})
        if isinstance(rows, list) and len(rows) > 250:
            out[c] = rows
    return out


def main():
    watch = hlx.WATCHLIST

    allrows = load(sorted(set(watch) | set(MAJORS)))
    print(f"loaded {len(allrows)} coins with >=250 daily bars")

    winners = []
    for c, rows in allrows.items():
        r = (float(rows[-1]["c"]) / float(rows[0]["c"]) - 1) * 100
        if r > 0:
            winners.append(c)
    universes = {
        "A. MAJORS": [c for c in MAJORS if c in allrows],
        "B. WINNERS": [c for c in winners if c not in MAJORS],
        "C. FULL": [c for c in allrows],
    }
    for k, v in universes.items():
        print(f"  {k}: {len(v)} coins -> {', '.join(v[:12])}{'...' if len(v) > 12 else ''}")
    print()

    kinds = ["mom7", "mom20", "mom30", "sma50", "trend200"]
    hdr = (f"{'universe':<12}{'rule':<9}{'trail':>6}{'coins':>6}{'trd':>5}{'win%':>6}"
           f"{'strat':>9}{'hold':>8}{'EDGE':>8}{'sDD':>7}{'hDD':>7}{'beat':>6}")
    print(hdr)
    print("-" * len(hdr))
    out = {}
    for uname, coins in universes.items():
        rows = {c: allrows[c] for c in coins if c in allrows}
        for trail in (2.5, 5.0, 8.0):
            SD.TRAIL_ATR = trail
            for k in kinds:
                res = [(c, SD.run_coin(r, k)) for c, r in rows.items()]
                res = [(c, r) for c, r in res if r["trades"] > 0]
                if not res:
                    continue
                med = lambda f: statistics.median([f(r) for _, r in res])
                beat = sum(1 for _, r in res if r["ret"] > r["bh"])
                tr = sum(r["trades"] for _, r in res)
                wr = sum(r["wins"] for _, r in res) / tr * 100 if tr else 0
                print(f"{uname:<12}{k:<9}{trail:>6.1f}{len(res):>6}{tr:>5}{wr:>5.0f}%"
                      f"{med(lambda r: r['ret']):>8.0f}%{med(lambda r: r['bh']):>7.0f}%"
                      f"{med(lambda r: r['ret'] - r['bh']):>+7.0f}%"
                      f"{med(lambda r: r['dd']):>6.0f}%{med(lambda r: r['bh_dd']):>6.0f}%"
                      f"{beat:>4}/{len(res)}")
                out[f"{uname}|{k}|{trail}"] = dict(
                    coins=len(res), trades=tr, win=wr,
                    strat=med(lambda r: r["ret"]), hold=med(lambda r: r["bh"]),
                    edge=med(lambda r: r["ret"] - r["bh"]), beat=beat)
        print()

    json.dump(out, open("study_daily_universe.json", "w"), indent=1)

    print("=" * 80)
    pos = {k: v for k, v in out.items() if v["edge"] > 5 and v["beat"] > v["coins"] / 2}
    if pos:
        print("CONFIGURATIONS THAT BEAT HOLDING ON MOST COINS (edge > +5%):")
        for k, v in sorted(pos.items(), key=lambda kv: -kv[1]["edge"]):
            print(f"  {k:<32} edge {v['edge']:+.0f}%  beat {v['beat']}/{v['coins']}"
                  f"  strat {v['strat']:+.0f}% vs hold {v['hold']:+.0f}%")
    else:
        print("NOTHING beat buy-and-hold on most coins. Timing added nothing anywhere tested.")
    print("\nwritten: study_daily_universe.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
