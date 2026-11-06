#!/usr/bin/env python3
"""Minimal, dependency-free read-only client for the Hyperliquid public API.

All a study needs in order to be reproducible: fetch candles, fetch marks, and the
coin universe the original work used. No keys, no orders, nothing account-specific -
the Hyperliquid info endpoint is public.
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "https://api.hyperliquid.xyz/info"

# The universe the studies were run against (the desk's live watchlist at the time:
# large-cap alts plus memecoins). Deliberately NOT "the best coins" - it is the list
# that was actually being traded, warts included. See README for why that matters.
WATCHLIST = ['HYPE', 'ZEC', 'SOL', 'JUP', 'WIF', 'POPCAT', 'PNUT', 'PENGU', 'FARTCOIN', 'GOAT', 'MOODENG', 'TRUMP', 'SPX', 'kBONK', 'TURBO', 'GRASS', 'PENDLE', 'LINK', 'AAVE', 'STX', 'CAKE', 'SUI', 'ENA', 'ONDO', 'AR', 'UNI', 'ENS', 'CASHCAT', 'PONS', 'VVV']


def post(payload, tries=4, timeout=45):
    """POST to the info endpoint with simple backoff - HL rate-limits bursts."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                API, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception as exc:               # noqa: BLE001 - retry anything transient
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"info request failed: {type(last).__name__}: {last}")


def candles(coin, interval, lookback_ms):
    r"""OHLCV bars, oldest first. interval is '15m' / '1h' / '1d' etc.

    HL answers a burst of candleSnapshot calls with HTTP 429, so callers should pace
    themselves; `post` retries, but a study sweeping 30 coins is still a burst.
    """
    now = int(time.time() * 1000)
    return post({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": interval,
        "startTime": now - int(lookback_ms), "endTime": now}})


def all_mids():
    """{coin: mark price} for every perp and spot pair."""
    return post({"type": "allMids"})


def meta():
    return post({"type": "meta"})


def meta_dec(coin):
    """szDecimals for a perp - the venue's lot-size precision."""
    for u in meta()["universe"]:
        if u["name"] == coin:
            return int(u.get("szDecimals", 0))
    return 0
