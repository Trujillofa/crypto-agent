# Volatility Squeeze Breakout Bounded — Surface v0 (Option F)

**Status:** BTC/ETH probe passed — implement per implementation brief (no campaign yet)
**Implementation brief:** `volatility-squeeze-breakout-bounded-implementation-brief-v0.md`
**Date:** 2026-06-05
**Symbol order:** BTCUSDT → ETHUSDT → SOLUSDT (SOL last for correlation hygiene)

---

## Hypothesis

Compressed Bollinger width (volatility squeeze) followed by upward expansion with
positive momentum produces tradeable long entries **distinct** from trend-pullback
and sentiment-macro timing.

---

## Entry (aligned with `VolatilitySqueezeStrategy`)

1. BB width percentile rank &lt; `squeeze_percentile` (default 0.20) over `squeeze_lookback` (50)
2. Close &gt; SMA(20)
3. Momentum (ROC over `momentum_period`) &gt; 0
4. `atr_pct` ≥ `min_atr_pct` (0.005)

Probe applies cooldown: no re-entry for `max(12,24,48)` bars after each event.

---

## Feasibility probe

```bash
uv run python -m scripts.probe_volatility_squeeze_breakout --symbol BTCUSDT
./scripts/run_option_f_squeeze_probe.sh   # BTC → ETH → SOL on prod
```

**Proceed to implementation only if:**

- ≥ 20 events per symbol tested
- Positive mean forward return on at least one of 12h / 24h / 48h horizons
- Max single-event share of positive returns ≤ 30%

---

## Production probe result — 2026-06-05

Source: `docs/reports/volatility-squeeze-breakout-probe-2026-06-05.md`

| Symbol | Events | 12h mean | 24h mean | 48h mean | Verdict |
|--------|--------|----------|----------|----------|---------|
| BTCUSDT | 176 | +0.02% | -0.06% | -0.19% | `HAS_PULSE` |
| ETHUSDT | 241 | +0.15% | -0.20% | -0.51% | `HAS_PULSE` |
| SOLUSDT | 269 | -0.03% | -0.23% | -0.27% | `WEAK_EDGE` |

Decision: proceed only with a bounded **BTC/ETH** implementation brief. Do not
include SOL in the first campaign; it adds correlation with the live SOL overlay
and did not show positive forward mean.

---

## Implementation (next)

See `volatility-squeeze-breakout-bounded-implementation-brief-v0.md`:

- Family: `volatility_squeeze_bounded` (reuse `volatility_squeeze`, 12h hold + `time_stop_minutes`)
- Campaign: 40 runs BTC + 40 runs ETH (separate lanes)
- Gates: unchanged (`standard` → `promotion_candidate` → b=1000)
- Overlap: vs SOL overlay live + sentiment-macro before promotion

---

## Closed paths (do not repeat)

- Funding-normalization SOL (Wave 9): 0/80 standard passes
- Relative-strength ETH/BTC 1h probe: sparse, negative excess
- ETH 4h bounded b=1000: skipped (formal reject not needed)
