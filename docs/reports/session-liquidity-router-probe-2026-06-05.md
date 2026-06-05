# Session Liquidity Router Probe — 2026-06-05

**Purpose:** cheap feasibility check before building a session router or launching
another autoresearch campaign.

**Runner:** `scripts/probe_session_liquidity_router.py` on Hetzner production DB.

**Data:** production TimescaleDB, 1h bars, 2024-01-01 to 2026-06-01.

**Forward horizon:** 12 bars (12h).

**Gate for pulse:**

- At least 1,000 eligible bars total
- At least 500 samples per session window
- A session beats the all-hours baseline on both mean forward return and mean
  long adverse excursion

## Result

| Symbol | Baseline 12h mean | Baseline MAE | Best window | Window 12h mean | Window MAE | Window win rate | Verdict |
|--------|-------------------|--------------|-------------|-----------------|------------|-----------------|---------|
| BTCUSDT | +0.042% | 1.297% | `americas` | +0.058% | 1.175% | 53.4% | `HAS_PULSE` |
| ETHUSDT | +0.025% | 1.898% | `americas` | +0.117% | 1.727% | 54.8% | `HAS_PULSE` |
| SOLUSDT | +0.034% | 2.360% | `americas` | +0.168% | 2.160% | 53.7% | `HAS_PULSE` |

Each symbol had 20,496 eligible bars. Session sample counts were balanced:

- `asia`: 6,832 samples
- `europe`: 6,837 samples
- `americas`: 6,827 samples

## Read

The `americas` window (16:00-24:00 UTC) is the only favorable disjoint session
across BTC, ETH, and SOL. It improves both required metrics:

- higher 12h mean forward return than the all-hours baseline
- lower mean long adverse excursion than the all-hours baseline

This is a better cheap-probe result than Wave 9 funding normalization or Wave 10
volatility squeeze because it is not SOL-only and does not depend on a new entry
pattern. It is still not a deployable edge. The result says "time-of-day gating
has a pulse"; it does not prove that any live strategy improves after gating.

## Decision

Proceed to a **Session Liquidity Router implementation brief**.

The v1 router should be an entry gate, not a new signal:

- allowed window: `americas`
- timeframe: 1h
- action: convert BUY to HOLD outside allowed windows
- first target: shadow/paper comparison on the existing SOL overlay stack
- second target: WFO overlay family only if the shadow/probe evidence holds

Do not deploy a live router directly from this probe. The next validation step is
whether blocking non-Americas entries improves existing strategy risk metrics
without killing trade frequency.

## Operational note

The first prod attempt exposed a probe bug: CLI `--start` / `--end` values were
passed to asyncpg as strings. Fixed in `4638361` by coercing ISO timestamps to
`datetime` before DB binding.
