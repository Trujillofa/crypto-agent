# Volatility Squeeze Breakout Bounded — Surface v0 (Option F)

**Status:** feasibility probe only — no autoresearch until `HAS_PULSE`
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

## If probe passes (later)

- Family: `volatility_squeeze_bounded` (standalone, time-stop in overlay)
- Gates: unchanged (`standard` → `promotion_candidate` → b=1000)
- Overlap: vs SOL overlay live + sentiment-macro before promotion

---

## Closed paths (do not repeat)

- Funding-normalization SOL (Wave 9): 0/80 standard passes
- Relative-strength ETH/BTC 1h probe: sparse, negative excess
- ETH 4h bounded b=1000: skipped (formal reject not needed)
