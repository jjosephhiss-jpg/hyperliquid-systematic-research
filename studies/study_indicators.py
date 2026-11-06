#!/usr/bin/env python3
"""Event study: does adding indicators to the momentum gates improve the win rate?

Replays ~50 days of 15m Hyperliquid candles for the desk's live watchlist. At every
bar it asks "would the engine have entered here?" using ONLY data up to that bar,
then walks forward to see whether the +3% target or the -4.5% stop was hit first.

The live desk's problem is arithmetic: TP +3% / SL -4.5% needs a 60% win rate to
break even, and the record sits at ~59%. So the question this answers is narrow and
honest — does any indicator filter lift the win rate above that line?

Outputs a table to stdout and writes study_results.json.
"""
from __future__ import annotations

import json
import os
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

# live doctrine
G24, G24MAX, G6, G1, G1MAX = 2.0, 15.0, 0.5, 0.3, 2.0
TP_FRAC, SL_FRAC = 1.03, 0.955
FEE_PCT = 0.09          # round-trip taker, %
HORIZON = 192           # bars to wait for TP/SL (48h at 15m)
WARMUP = 210            # bars needed before any signal (for EMA50 + MACD + ATR)


def post(payload):
    req = urllib.request.Request(API, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=45))


def candles(coin, days=52, interval="15m"):
    now = int(time.time() * 1000)
    rows = post({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": interval,
        "startTime": now - days * 86400000, "endTime": now}})
    if not isinstance(rows, list) or len(rows) < 50:
        return None
    return rows


VARIANTS = [
    ("baseline (momentum only)", lambda f: True),
    ("+ RSI < 70", lambda f: f["rsi"] < 70),
    ("+ RSI < 60", lambda f: f["rsi"] < 60),
    ("+ close > EMA50", lambda f: f["close"] > f["ema50"]),
    ("+ close > EMA200", lambda f: f["close"] > f["ema200"]),
    ("+ MACD hist > 0", lambda f: f["macd_hist"] > 0),
    ("+ %B < 1.0 (not above band)", lambda f: f["pctb"] < 1.0),
    ("+ stoch %K < 80", lambda f: f["stoch_k"] < 80),
    ("+ close > VWAP96", lambda f: f["close"] > f["vwap96"]),
    ("+ EMA50 > EMA200", lambda f: f["ema50"] > f["ema200"]),
    ("ALL: RSI<70 & EMA50 trend & MACD>0",
     lambda f: f["rsi"] < 70 and f["close"] > f["ema50"] and f["macd_hist"] > 0),
    ("TIGHT: RSI<60 & EMA50>EMA200 & MACD>0",
     lambda f: f["rsi"] < 60 and f["ema50"] > f["ema200"] and f["macd_hist"] > 0),
]


def study_coin(coin):
    rows = candles(coin)
    if not rows:
        return None
    o = np.array([float(r["o"]) for r in rows])
    h = np.array([float(r["h"]) for r in rows])
    l = np.array([float(r["l"]) for r in rows])
    c = np.array([float(r["c"]) for r in rows])
    v = np.array([float(r["v"]) for r in rows])
    n = len(c)
    if n < WARMUP + HORIZON:
        return None

    feats = {
        "rsi": ind.rsi(c, 14),
        "ema50": ind.ema(c, 50),
        "ema200": ind.ema(c, 200),
        "pctb": ind.bollinger(c, 20, 2.0)[3],
        "atr": ind.atr(h, l, c, 14),
        "stoch_k": ind.stochastic(h, l, c, 14, 3)[0],
        "vwap96": ind.vwap(h, l, c, v, window=96),
    }
    _, _, macd_hist = ind.macd(c)
    feats["macd_hist"] = macd_hist

    trades = []
    i = WARMUP
    while i < n - 2:
        # --- momentum gates, causal: only closes at or before i ---
        c24 = (c[i] / c[i - 96] - 1) * 100 if i - 96 >= 0 else None
        c6 = (c[i] / c[i - 24] - 1) * 100 if i - 24 >= 0 else None
        c1 = (c[i] / c[i - 4] - 1) * 100 if i - 4 >= 0 else None
        if c24 is None or c6 is None or c1 is None:
            i += 1
            continue
        if not (G24 <= c24 <= G24MAX and c6 >= G6 and G1 <= c1 <= G1MAX):
            i += 1
            continue

        f = {k: float(vv[i]) for k, vv in feats.items()}
        f["close"] = float(c[i])
        if any(np.isnan(x) for x in f.values()):
            i += 1
            continue

        entry = float(c[i])
        tp = entry * TP_FRAC
        sl = entry * SL_FRAC
        atr = f["atr"]
        # ATR variant: stop 1.5 ATR below entry, target 2.25 ATR above (1.5 R:R)
        atr_sl = entry - 1.5 * atr
        atr_tp = entry + 2.25 * atr

        outcome = {"idx": i, "entry": entry, "t": rows[i]["t"]}
        exit_i = None
        for j in range(i + 1, min(i + 1 + HORIZON, n)):
            hit_sl = l[j] <= sl
            hit_tp = h[j] >= tp
            if hit_sl:                      # conservative: stop wins ties
                outcome.update(result=-1, bars=j - i, why="sl"); exit_i = j; break
            if hit_tp:
                outcome.update(result=1, bars=j - i, why="tp"); exit_i = j; break
        if exit_i is None:
            j = min(i + HORIZON, n - 1)
            ret = (c[j] / entry - 1) * 100
            outcome.update(result=0, bars=j - i, why="timeout", ret=ret)
            exit_i = j

        # per-variant flags
        outcome["flags"] = {name: bool(fn(f)) for name, fn in VARIANTS}

        # ATR-stop variant outcome, walked separately
        a_res = 0
        a_ret = None
        a_exit = None
        for j in range(i + 1, min(i + 1 + HORIZON, n)):
            if l[j] <= atr_sl:
                a_res, a_exit = -1, j; break
            if h[j] >= atr_tp:
                a_res, a_exit = 1, j; break
        if a_exit is None:
            j = min(i + HORIZON, n - 1)
            a_ret = (c[j] / entry - 1) * 100
            a_exit = j
        if a_ret is None:
            a_ret = (atr_tp / entry - 1) * 100 if a_res == 1 else (atr_sl / entry - 1) * 100
        outcome["atr"] = {"result": a_res, "ret": a_ret, "bars": a_exit - i}

        trades.append(outcome)
        i = exit_i + 1        # no overlapping trades on the same coin

    return coin, trades


def summarise(all_trades, name):
    sel = [t for t in all_trades if t["flags"][name]]
    if not sel:
        return dict(n=0)
    wins = [t for t in sel if t["result"] == 1]
    losses = [t for t in sel if t["result"] == -1]
    timeouts = [t for t in sel if t["result"] == 0]
    rets = []
    for t in sel:
        if t["result"] == 1:
            rets.append((TP_FRAC - 1) * 100)
        elif t["result"] == -1:
            rets.append((SL_FRAC - 1) * 100)
        else:
            rets.append(t["ret"])
    net = [r - FEE_PCT for r in rets]
    decided = len(wins) + len(losses)
    return dict(
        n=len(sel), wins=len(wins), losses=len(losses), timeouts=len(timeouts),
        win_rate=(len(wins) / decided * 100) if decided else float("nan"),
        avg_ret=float(np.mean(rets)), avg_net=float(np.mean(net)),
        total_net=float(np.sum(net)),
    )


def main():
    watch = json.load(open("/tmp/wl.json")) if len(sys.argv) > 1 else None
    if watch is None:
        import re
    watch = hlx.WATCHLIST

    all_trades = []
    per_coin = {}
    for coin in watch:
        try:
            res = study_coin(coin)
        except Exception as exc:
            print(f"  {coin:9s} skipped: {exc}")
            continue
        if not res:
            continue
        c, tr = res
        per_coin[c] = len(tr)
        all_trades.extend(tr)
        print(f"  {c:9s} {len(tr):3d} signals", flush=True)

    if not all_trades:
        print("no signals found")
        return 1

    print(f"\nTotal signals across {len(per_coin)} coins: {len(all_trades)}")
    decided = [t for t in all_trades if t["result"] in (1, -1)]
    if decided:
        wr = sum(1 for t in decided if t["result"] == 1) / len(decided) * 100
        print(f"Baseline win rate (all variants' pool): {wr:.1f}% "
              f"({sum(1 for t in decided if t['result']==1)}W/"
              f"{sum(1 for t in decided if t['result']==-1)}L)")

    print(f"\n{'variant':38s} {'n':>5} {'W':>4} {'L':>4} {'timeout':>8} "
          f"{'win%':>6} {'avg%':>7} {'net avg%':>9} {'total%':>8}")
    print("-" * 98)
    rows = []
    for name, _ in VARIANTS:
        s = summarise(all_trades, name)
        if not s["n"]:
            print(f"{name:38s} {0:>5}")
            continue
        print(f"{name:38s} {s['n']:>5} {s['wins']:>4} {s['losses']:>4} "
              f"{s['timeouts']:>8} {s['win_rate']:>5.1f}% {s['avg_ret']:>+7.2f} "
              f"{s['avg_net']:>+9.3f} {s['total_net']:>+8.2f}")
        rows.append((name, s))

    # ATR-stop variant on the baseline pool
    atr_sel = all_trades
    a_w = sum(1 for t in atr_sel if t["atr"]["result"] == 1)
    a_l = sum(1 for t in atr_sel if t["atr"]["result"] == -1)
    a_rets = [t["atr"]["ret"] - FEE_PCT for t in atr_sel]
    print("\nATR-based stop (SL 1.5xATR, TP 2.25xATR, 1.5 R:R) on the same entries:")
    print(f"  n={len(atr_sel)} W={a_w} L={a_l} "
          f"win%={(a_w/(a_w+a_l)*100) if a_w+a_l else float('nan'):.1f} "
          f"avg net%={np.mean(a_rets):+.3f} total net%={np.sum(a_rets):+.1f}")
    print(f"  breakeven win rate for 1.5 R:R = {1.5/(1+1.5)*100:.0f}%")

    json.dump({"per_coin": per_coin,
               "variants": {n: s for n, s in rows},
               "atr": {"n": len(atr_sel), "w": a_w, "l": a_l,
                       "avg_net": float(np.mean(a_rets)),
                       "total_net": float(np.sum(a_rets))}},
              open("study_results.json", "w"), indent=1)
    print("\nwritten: study_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
