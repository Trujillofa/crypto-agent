# Lane Brief — Higher-Timeframe Trend-Following (long-biased) Probe v0

**Status:** Gate 0 (brief) → Gate 1 (cheap probe) pending
**Script:** `scripts/probe_higher_tf_trend_following.py`
**Related:** [research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md),
[RBI_AUTORESEARCH_LOOP.md](../RBI_AUTORESEARCH_LOOP.md)

---

## Edge thesis

On BTC/ETH/SOL the only behaviour that has *ever* produced real PnL in this system is
**long exposure during a sustained uptrend** (live `sentiment-macro-bot`, March 2026:
16 long trades, +$341 — the single largest profit chunk). Every mean-reversion / fade
lane has lost, because these assets **trend**: short-horizon reversion fights the drift.

Hypothesis: a **daily-trend long-only filter** — "hold long while the daily trend is up,
flat otherwise" — beats buy-and-hold on a **risk-adjusted** basis (higher Sharpe, lower
max drawdown) by capturing trend legs while sitting out chop/downtrends. It does not try
to time entries on 1h structure; it holds positions over multi-day trend regimes.

## Why this primitive is different from banned lanes

| Banned / closed | This lane |
|---|---|
| 1h single-symbol OHLCV structure (sweep/range-break) | **Daily** regime, position-held over days |
| Mean-reversion / fade (short crowding, sweep fade, sentiment shorts) | **Trend-following / continuation, long-only** — the opposite, and the direction the data supports |
| SOL overlay aggregator/threshold tuning | Not attached to the overlay; standalone trend state |
| Funding/premium/session label filters | No new label; pure higher-TF price trend |

It satisfies the reset doc's allowed-family rules: different primitive, cheap probe
first, standalone surface, higher-TF (different horizon), long-biased (does not add
short directionality but *is* the regime the evidence rewards).

## Expected regime

Works in trending markets (2024–2025 BTC/ETH/SOL up-legs; March 2026). Expected to
**underperform buy-and-hold in raw return** during relentless bull runs (it sits out some
upside on whipsaws) but to **cut drawdown** in chop/bear phases — the risk-adjusted win.

## Expected failure mode

- Trend filter whipsaws in range markets → fees + missed re-entries erode the DD benefit
  (WEAK_EDGE: lower DD but Sharpe not improved).
- Edge only appears at one specific MA length → fragile / overfit (WEAK_EDGE).
- On these symbols buy-and-hold simply dominates risk-adjusted → NO_PULSE.

## Signal definition

- Daily bars (resampled from 1h, UTC midnight buckets).
- Trend state at end of day *t*: `close[t] > SMA(window)[t]` → long for day *t+1*, else flat.
- Canonical windows tested for robustness (no search-for-best): **SMA 50 / 100 / 200**.
- One-way fee applied on each state change.
- Compared against buy-and-hold over the identical window.

## Target symbol / timeframe

BTC/ETH/SOLUSDT, **daily** (resampled from `ohlcv` 1h), window 2024-01-01 → 2026-06-01.

## Target trade density

Regime switches per symbol are the proxy for live trade count. A daily SMA filter
typically switches on the order of **monthly**, i.e. tens of switches over the window —
deliberately chosen so that, unlike the 0-trade SOL overlay, a promoted version could
**accumulate enough live trades to forward-validate** within a reasonable horizon. The
probe reports switch counts explicitly.

## Independence expectation vs live agents

- `agent_sol_1h_trend_pullback_overlay_live`: 0 trades — no overlap in practice.
- `agent_sentiment_macro`: currently short-biased / mean-reverting — a long trend filter
  is directionally *opposite*, so low entry overlap and genuine diversification.

## Validation command plan

```bash
# Gate 1 cheap probe (read-only OHLCV)
uv run python scripts/probe_higher_tf_trend_following.py --json

# Guard the next allowed action from artifacts
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/higher-tf-trend-following-probe-v0.md \
  --probe-verdict <HAS_PULSE|WEAK_EDGE|NO_PULSE> \
  --pretty
```

- `HAS_PULSE` → write a bounded standalone daily-trend strategy surface, then Gate 2
  autoresearch (config-only) under the standard gate.
- `WEAK_EDGE` / `NO_PULSE` → close the lane, record in the candidate ledger; do **not**
  reshape into more MA-length fishing.

## Pulse criteria (encoded in the probe)

Per (symbol, MA window) a "pass" requires, vs buy-and-hold over the same window:
- Sharpe(strategy) ≥ Sharpe(buy-hold), **and**
- max drawdown(strategy) < max drawdown(buy-hold), **and**
- total return(strategy) > 0.

Aggregate verdict:
- **HAS_PULSE** — for at least one MA window, a strict majority of symbols pass (robust
  across symbols on the *same* rule).
- **WEAK_EDGE** — some passes but no single rule clears a symbol majority (fragile).
- **NO_PULSE** — no symbol/rule passes.
