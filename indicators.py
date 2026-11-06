#!/usr/bin/env python3
"""Technical indicators for the HL desk — numpy only, no pandas dependency.

Everything takes plain sequences (lists or numpy arrays) of closes/highs/lows/volumes
and returns a numpy float array the same length as the input, NaN-padded at the front
where the indicator has not warmed up yet. That alignment rule is deliberate: a signal
at index i must only ever be computed from data at or before index i, so a backtest
cannot leak the future.

Included (the standard set, as advertised by every "free TradingView" app):
    SMA, EMA, Bollinger Bands, VWAP, RSI (Wilder), MACD, Stochastic, ATR (Wilder)

Usage:
    from indicators import sma, ema, rsi, macd, bollinger, atr, stochastic, vwap
    r = rsi(closes, 14)          # last value: r[-1]

Self-test:  python indicators.py
"""
from __future__ import annotations

import numpy as np


def _arr(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if a.ndim != 1:
        raise ValueError("expected a 1-D series")
    return a


def sma(values, period: int) -> np.ndarray:
    """Simple moving average."""
    x = _arr(values)
    out = np.full(x.shape, np.nan)
    if period < 1 or len(x) < period:
        return out
    csum = np.cumsum(np.insert(x, 0, 0.0))
    out[period - 1:] = (csum[period:] - csum[:-period]) / period
    return out


def ema(values, period: int) -> np.ndarray:
    """Exponential moving average, seeded with the SMA of the first `period` values.

    Seeding with the SMA (rather than the first value) is the convention TradingView
    uses; it makes our numbers comparable to what the user sees on a chart.
    """
    x = _arr(values)
    out = np.full(x.shape, np.nan)
    if period < 1 or len(x) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = x[:period].mean()
    for i in range(period, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def rsi(values, period: int = 14) -> np.ndarray:
    """Relative Strength Index using Wilder's smoothing."""
    x = _arr(values)
    out = np.full(x.shape, np.nan)
    if len(x) <= period:
        return out
    delta = np.diff(x)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = gain[:period].mean()
    avg_loss = loss[:period].mean()
    def _rsi(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        rs = g / l
        return 100.0 - (100.0 / (1.0 + rs))
    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(x)):
        avg_gain = (avg_gain * (period - 1) + gain[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i - 1]) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def macd(values, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line, signal line and histogram."""
    x = _arr(values)
    macd_line = ema(x, fast) - ema(x, slow)
    # signal EMA must ignore the NaN warm-up region
    start = slow - 1
    hist = np.full(x.shape, np.nan)
    sig = np.full(x.shape, np.nan)
    if len(x) <= start:
        return macd_line, sig, hist
    tail = macd_line[start:]
    sig_tail = ema(tail, signal)
    sig[start:] = sig_tail
    hist = macd_line - sig
    return macd_line, sig, hist


def bollinger(values, period: int = 20, k: float = 2.0):
    """Bollinger Bands: middle (SMA), upper, lower, plus %B position (0..1 typical)."""
    x = _arr(values)
    mid = sma(x, period)
    sd = np.full(x.shape, np.nan)
    for i in range(period - 1, len(x)):
        sd[i] = x[i - period + 1:i + 1].std(ddof=0)
    upper = mid + k * sd
    lower = mid - k * sd
    width = upper - lower
    with np.errstate(invalid="ignore", divide="ignore"):
        pct_b = np.where(width > 0, (x - lower) / width, np.nan)
    return upper, mid, lower, pct_b


def atr(high, low, close, period: int = 14) -> np.ndarray:
    """Average True Range, Wilder's smoothing."""
    h, l, c = _arr(high), _arr(low), _arr(close)
    n = len(c)
    out = np.full(c.shape, np.nan)
    if n < 2 or len(h) != n or len(l) != n:
        return out
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    if n < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def stochastic(high, low, close, k_period: int = 14, d_period: int = 3):
    """Stochastic oscillator %K and %D (0..100)."""
    h, l, c = _arr(high), _arr(low), _arr(close)
    n = len(c)
    k = np.full(c.shape, np.nan)
    for i in range(k_period - 1, n):
        hh = h[i - k_period + 1:i + 1].max()
        ll = l[i - k_period + 1:i + 1].min()
        k[i] = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100.0
    d = sma(k, d_period)
    return k, d


def vwap(high, low, close, volume, window: int | None = None) -> np.ndarray:
    """Volume-weighted average price.

    window=None -> cumulative VWAP from the start of the series (session-style).
    window=N    -> rolling VWAP over the last N bars, which is the useful form for a
                  24/7 perp market with no session boundary (Hyperliquid).
    """
    h, l, c, v = _arr(high), _arr(low), _arr(close), _arr(volume)
    tp = (h + l + c) / 3.0
    pv = tp * v
    out = np.full(c.shape, np.nan)
    if window is None:
        cv = np.cumsum(v)
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(cv > 0, np.cumsum(pv) / cv, np.nan)
        return out
    for i in range(window - 1, len(c)):
        vs = v[i - window + 1:i + 1].sum()
        out[i] = pv[i - window + 1:i + 1].sum() / vs if vs > 0 else np.nan
    return out


# ---------------------------------------------------------------- self-test
def _selftest():
    ok = True

    def check(name, got, want, tol=1e-6):
        nonlocal ok
        good = abs(float(got) - float(want)) <= tol
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {name}: got {got:.6f} want {want:.6f}")

    x = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    check("sma(3) last", sma(x, 3)[-1], 9.0)
    check("sma(3) first valid index", np.isnan(sma(x, 3)[1]) and not np.isnan(sma(x, 3)[2]), True)

    # EMA seeded with SMA(3) of 1,2,3 -> 2.0 at index 2, then alpha=0.5
    e = ema(x, 3)
    check("ema(3) seed", e[2], 2.0)
    check("ema(3) next", e[3], 0.5 * 4 + 0.5 * 2.0)

    # RSI on a monotonically rising series must be exactly 100
    up = list(range(1, 40))
    check("rsi rising 100", rsi(up, 14)[-1], 100.0, tol=1e-9)
    down = list(range(40, 1, -1))
    check("rsi falling 0", rsi(down, 14)[-1], 0.0, tol=1e-9)

    # ATR of a series with constant range 2 must equal 2 once warm
    h = [10 + i for i in range(30)]
    l = [8 + i for i in range(30)]
    c = [9 + i for i in range(30)]
    check("atr constant range", atr(h, l, c, 14)[-1], 2.0, tol=1e-6)

    # Bollinger %B of a flat series is NaN-safe; of the mid point ~0.5
    flat = [5.0] * 30
    _, _, _, pb = bollinger(flat, 20, 2)
    check("bollinger flat -> pctB nan", np.isnan(pb[-1]), True)
    ramp = list(range(1, 41))
    upper, mid, lower, pct_b_ramp = bollinger(ramp, 20, 2)
    check("bollinger mid is sma", mid[-1], np.mean(ramp[-20:]))

    # VWAP with equal volume equals the mean price
    tp = [10.0] * 5
    check("vwap equal vol", vwap(tp, tp, tp, [1] * 5, window=5)[-1], 10.0)

    # Stochastic of a close at the top of range = 100
    hh = [10, 11, 12, 13, 14]
    ll = [8, 9, 10, 11, 12]
    cc = [10, 11, 12, 13, 14]
    check("stoch at high = 100", stochastic(hh, ll, cc, 5, 3)[0][-1], 100.0)

    print("\nALL PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
