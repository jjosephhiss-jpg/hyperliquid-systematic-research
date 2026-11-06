#!/usr/bin/env python3
"""Adaptive (volatility-scaled) stops - the one exit dimension never actually tested.

WHY THIS IS NOT A RE-TREAD. study_stops.py swept the stop WIDTH, but applied the SAME
width to every coin. That is the real flaw in the live desk too: it puts a flat -4.5%
stop on a coin whose daily range is 1.5% (where the stop is never reached, so a loser
is held to the close) AND on one whose daily range is 10% (where -4.5% is inside the
noise and gets hit constantly). Scaling the stop to each coin's own ATR is a different
hypothesis from "wider" or "tighter", and it has a clear mechanism.

THE TEST THAT MATTERS. A wider stop mechanically reduces stop-outs while increasing the
size of each loss - so a naive "% expectancy" comparison can flatter any wider stop for
free. Two guards against fooling ourselves:
  1. R-MULTIPLE COLUMN. Score every trade as outcome/stop_distance, so all variants are
     compared at equal risk per trade rather than equal dollars.
  2. ITS OWN FLAT EQUIVALENT. For each ATR multiplier, also simulate a FLAT stop set to
     the average width that variant produced. If ATR-k does not beat flat-at-the-same-
     average-width, then the adaptivity itself adds nothing and we should stop looking.

Also run twice: plain (fixed stop + fixed TP) and with the live profit-ratchet, because
the live desk has a tight 0.75%/0.4% trail that can fire long before a wide stop matters.

Entries and target are held identical to live: gates 24h>=2 / 6h>=0.5 / no 1h floor /
24h<=15, TP +2%, adverse-first within a bar.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

import numpy as np
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import hlx

API = "https://api.hyperliquid.xyz/info"
TP_PCT = 2.0
ARM, TRAIL = 0.75, 0.4
ATMULT = (0.75, 1.0, 1.5, 2.0)
CLAMP = (2.0, 25.0)          # a dead coin must not get a 0.3% stop
HORIZON, WARMUP = 192, 210
FEE = 0.0916                 # round trip, taker


def post(p, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(API, data=json.dumps(p).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=45))
        except Exception:
            time.sleep(1.5 * (a + 1))


def vol24(h, l, c, n=96):
    """TRUE 24h high-low range as a % of price, causal at every bar.

    NOT Wilder ATR: ATR is the mean TRUE RANGE PER BAR, so on 15m bars it returns the
    typical 15-MINUTE range (~0.7%), not the daily one. Multiplying that by 1.5 built
    stops of ~1%, which is far tighter than anything intended. The honest volatility
    measure for "how far does this coin move in a day" is the 24h high-low span.
    """
    from numpy.lib.stride_tricks import sliding_window_view
    m = len(c)
    out = np.full(m, np.nan)
    if m < n:
        return out
    vh = sliding_window_view(h, n).max(axis=1)
    vl = sliding_window_view(l, n).min(axis=1)
    out[n - 1:] = (vh - vl) / c[n - 1:] * 100.0
    return out


def simulate(entry, hs, ls, stop_pct, ratchet):
    """Return % outcome (adverse-first)."""
    tp = entry * (1 + TP_PCT / 100)
    stop = entry * (1 - stop_pct / 100)
    peak = 0.0
    for k in range(len(hs)):
        if ls[k] <= stop:
            return (stop / entry - 1) * 100
        if hs[k] >= tp:
            return TP_PCT
        if ratchet:
            hi = (hs[k] / entry - 1) * 100
            if hi > peak:
                peak = hi
            if peak >= ARM:
                ns = entry * (1 + max(0.0, peak - TRAIL) / 100)
                if ns > stop:
                    stop = ns
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
        a = vol24(h, l, c, 96)
        n = len(c)
        i = WARMUP
        while i < n - 13:
            c24 = (c[i] / c[i - 96] - 1) * 100
            c6 = (c[i] / c[i - 24] - 1) * 100
            c1 = (c[i] / c[i - 4] - 1) * 100
            if 2.0 <= c24 <= 15.0 and c6 >= 0.5 and c1 <= 2.0 and not np.isnan(a[i]):
                j = min(i + 1 + HORIZON, n)
                sigs.append((rows[i]["t"], float(c[i]), float(a[i]),
                             h[i + 1:j], l[i + 1:j]))
                i += 96
            else:
                i += 1
    print(f"signals: {len(sigs)}  (vol = TRUE 24h high-low range, causal)")
    if not sigs:
        return 1
    atrs = np.array([s[2] for s in sigs])
    print(f"24h range % across signals: median {np.median(atrs):.2f}  "
          f"p10 {np.percentile(atrs,10):.2f}  p90 {np.percentile(atrs,90):.2f}  "
          f"range {atrs.min():.2f}-{atrs.max():.2f}")
    print("  -> a flat -4.5% stop is inside the noise for "
          f"{100*(atrs>4.5).mean():.0f}% of these signals, and unreachable for "
          f"{100*(atrs<2.25).mean():.0f}%")

    ts = sorted(s[0] for s in sigs)
    mid = ts[len(ts) // 2]

    for ratchet in (False, True):
        tag = "WITH live ratchet 0.75/0.4" if ratchet else "PLAIN (fixed stop + 2% TP)"
        print(f"\n{'='*94}\n{tag}\n{'='*94}")
        print(f"{'variant':34s}{'avg stop':>9}{'n':>6}{'avg%':>8}{'win%':>7}"
              f"{'R/trade':>9}  |{'early':>8}{'recent':>8}")
        print("-" * 94)

        def run(label, widths):
            res = np.array([simulate(s[1], s[3], s[4], w, ratchet) - FEE
                            for s, w in zip(sigs, widths)])
            r = res / np.array(widths)          # equal-risk, in R
            e = res[np.array([s[0] for s in sigs]) < mid]
            rc = res[np.array([s[0] for s in sigs]) >= mid]
            print(f"{label:34s}{np.mean(widths):>8.2f}%{len(res):>6}{res.mean():>+8.3f}"
                  f"{100*(res>0).mean():>6.1f}%{r.mean():>+9.3f}  "
                  f"|{e.mean():>+8.3f}{rc.mean():>+8.3f}")
            return r.mean()

        flat = run(f"FLAT {4.5}%  (live baseline)", [4.5] * len(sigs))
        for k in ATMULT:
            widths = [float(np.clip(k * s[2], *CLAMP)) for s in sigs]
            adaptive = run(f"ADAPTIVE {k} x 24h-range", widths)
            avgw = float(np.mean(widths))
            flat_eq = run(f"   ^ flat at same {avgw:.2f}%", [avgw] * len(sigs))
            verdict = ("adaptivity ADDS" if adaptive > flat_eq + 0.01 else
                       "adaptivity adds NOTHING")
            print(f"{'':34s}{verdict} (adaptive R {adaptive:+.3f} vs "
                  f"flat-at-same-width R {flat_eq:+.3f})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
