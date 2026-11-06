# Systematic trading on Hyperliquid: what we tested, and why none of it worked

Eleven systematic strategy families, tested against three years of Hyperliquid data with
proper out-of-sample splits. **The honest headline: none of them had a demonstrable edge.**
The only thing that made money over the period was owning assets with positive drift and
not selling them.

This repository is the methodology and the harness, published because negative results are
almost never written down. The internet is full of backtest equity curves and short on
"here is what didn't work and how we know".

---

## The results

### 1. The universe dominates the strategy

Three-year buy-and-hold, median per coin:

| | 3-year return |
|---|---|
| BTC | **+117%** |
| SOL | **+112%** |
| HYPE | **+505%** (701 days) |
| ETH | +18% |
| **Watchlist median** | **−41%** |
| GOAT / TRUMP / POPCAT / PNUT / FARTCOIN | −98% / −96% / −92% / −91% / −89% |

Only **10 of 28** coins had a positive three-year return. A momentum strategy on that basket
is buying each pump before each dump — which is precisely the −86%/year the daily momentum
study produced. **Before optimising a strategy, check the drift of the universe you are
running it on.** Cash returned 0% and beat the basket.

### 2. Momentum, intraday — no edge

Every entry filter, target, stop and gate that was tested failed an out-of-sample split.
Selected results (all-history % per trade, taker fees):

| Change tested | Result |
|---|---|
| Target sweep (+2% … +15%) | +2% marginally best; all others negative |
| Stop width sweep | negative at every width, in **both** halves |
| Added indicator filters (12 variants) | none survived the held-out half |
| Looser entry gates | no improvement |
| "Fade the pump" (short instead of long) | +0.012% — the exact mirror of long's −0.053%, i.e. noise |
| BTC regime filter (stand aside when BTC falls) | **made it worse** (−0.010 vs +0.029) |

The long/short mirror is the cleanest finding here: the two sides score almost exactly
opposite, and the sign flips between halves on both. A real edge does not invert when you
split the sample.

### 3. Momentum, daily — no edge, but an instructive pattern

Same rules on 3 years of daily bars. Results improved **monotonically as the trailing stop
widened** — 2.5×ATR catastrophic, 5.0× mediocre, 8.0× best in every universe:

| universe | rule | strat | hold | edge | beat |
|---|---|---|---|---|---|
| all 30 coins | mom30 @ 8×ATR | −40% | −49% | **+8%** | 19/30 |
| all 30 coins | sma50 @ 8×ATR | −32% | −49% | **+8%** | 19/30 |
| majors | mom30 @ 8×ATR | +102% | +79% | −13% | 2/4 |
| majors | 200-day filter | −26% | +79% | −111% | 0/4 |

Widening the trail converges the system on buy-and-hold — and **buy-and-hold is what
actually worked.** The best unbiased configuration still loses 32–40% over three years
with a −72% drawdown. Beating a losing benchmark is not an edge. On the majors, timing
the assets that go up destroyed value versus holding them.

### 4. Mean reversion, daily — the highest win rate, still no money

| universe | rule | win% | strat | hold |
|---|---|---|---|---|
| majors | RSI < 30 | 45% | −22% | +73% |
| majors | RSI(2) < 10 | 61% | −35% | +73% |
| majors | below lower Bollinger | 54% | **+4%** | +73% |
| all 30 | RSI < 30 | 58% | **+1%** | −52% |

Mean reversion wins **52–61%** of its trades — far better than momentum's 32–45% — and still
returns roughly nothing, because dip-buying catches falling knives: many small wins, a few
large losses. **A high win rate is not an edge.** On majors every rule lost badly to simply
holding, because a dip-buyer sits out 70–94% of the time and misses the drift.

### 5. Costs and mechanics, measured on a live account

- **0.0458% per side** paid in practice (140 fills) — **0.0916% round trip**, or 1.28% of the
  account in pure friction over the period
- Maker orders would cut this to ~0.03% round trip: worth **+0.062% per trade**, which is
  *larger than any edge the other studies found*
- **The −4.5% stop sits inside the noise for 82% of entries** — median 24h high-low range
  across signals is 6.83%, so the stop is rarely what closes a losing trade; the time-based
  dead-trade cut is
- Fee drag was eating roughly 60% of the entire measured edge

---

## Mistakes, including ours

Two errors are documented in the code and worth repeating, because both flatter the results:

**A look-ahead trap we caught in our own work.** Selecting the coins with a positive
three-year return and *then* testing a long-only rule on them produced a "+20% edge, beats
hold on 5/8" result. That is selection on the outcome, and it is not evidence. Only the
unbiased runs (all 30 coins) are reported as findings.

**A units error.** Wilder ATR on 15-minute bars returns the typical **15-minute** range
(~0.7%), not the daily one. Multiplying it produced 1% stops instead of volatility stops
and an entire first run of the ATR study was invalid. For "how far does this move in a day",
use the 24h high-low span.

**Scope of the claim.** This says *simple systematic rules on three years of Hyperliquid
daily data have no demonstrable edge*. It does not prove trading is impossible, and three
years is one crypto cycle. Both directions overclaim; treat this as what was measured.

---

## What is in here

```
hlx.py                  minimal read-only Hyperliquid client (no keys, public endpoints)
indicators.py           numpy-only SMA/EMA/Bollinger/VWAP/RSI/MACD/Stochastic/ATR
funding_watch.py        funding-rate scanner (break-even hours, not headline APR)
studies/
  study_targets.py      take-profit sweep
  study_stops.py        stop-width sweep
  study_gates.py        looser entry gates
  study_indicators.py   twelve indicator filters, plus study_oos.py for the held-out half
  study_entry_timing.py post-entry return distribution
  study_fixes.py        short-the-pump; and majors on 1h/4h
  study_ratchet.py      trailing-stop modelling
  study_execution.py    fee model, and a BTC regime filter
  study_atr_stops.py    volatility-scaled vs flat stops
  study_daily.py        the timescale test, with buy-and-hold as benchmark
  study_daily_universe.py majors vs winners vs full, across trail widths
  study_reversion.py    mean reversion on daily bars
```

## Running it

No dependencies beyond Python 3.11+ and numpy. The Hyperliquid info endpoint is public —
**no API key, no account, nothing to sign.**

```bash
python3 studies/study_daily.py            # timescale test (needs ~1000 daily bars)
python3 studies/study_reversion.py        # mean reversion
python3 studies/study_targets.py          # intraday target sweep (slower; ~30 coins x 52d)
```

Each study writes a JSON of its results next to itself. Run them from anywhere.

## Method

- **Out-of-sample by construction.** Every sweep splits history at the median timestamp and
  reports both halves separately, so a result that only works in one half is visible as such.
- **No look-ahead.** Signals are computed only from bars at or before index *i*; entries are
  taken at bar *i+1*'s open.
- **Adverse-first within a bar.** When a bar contains both the stop and the target, the stop
  is assumed to have hit first. Pessimistic on purpose.
- **Buy-and-hold is the benchmark**, not zero. On a rising asset a long-only rule inherits the
  drift and would otherwise look like skill.
- **Cash is the floor.** A rule that returns less than 0% has failed regardless of its edge
  over hold.

## What this is not

Not a trading system, not financial advice, and not an invitation to run anything live. It is
a record of eleven things that did not work, published so the next person can skip them.

There is no live engine here on purpose: a deployable bot with no measured edge is a way for
someone else to lose money, and there is enough of that already.

## Licence

MIT — see `LICENSE`.
