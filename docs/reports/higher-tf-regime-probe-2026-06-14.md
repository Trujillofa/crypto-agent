# higher-tf-regime-probe — Gate 1 probe execution (real-DB run 2026-06-15)

**Verdict (verbatim):** NO_PULSE

> Note: the original 2026-06-14 entry recorded a *synthetic dry-run* (the DB was
> unreachable in the builder session). This file now records the **real-DB run**
> (2026-06-15, reviewer cycle). The synthetic numbers are superseded.

**Command executed (real TimescaleDB via `IndicatorReader.fetch_multi_timeframe`):**
```
uv run python scripts/probe_higher_tf_regime.py \
  --symbols SOLUSDT,BTCUSDT,ETHUSDT \
  --base-timeframe 1h \
  --regime-timeframes 4h,1d \
  --start 2024-01-01 \
  --fee-pct 0.08 \
  --slippage-pct 0.02 \
  --verdict-output research/rbi_loop/higher-tf-regime-allocator-v0/probe-verdict.json
```

**Data provenance (Gate 0):** isolated local TimescaleDB seeded from Binance
historical klines: SOL/BTC/ETH at 1h (~43.4k rows each), 4h (~10.3k each, from
2021-10), 1d (1,090 each, from 2023-06). Indicators + 007 regime features computed
via `scripts/compute_historical_indicators.py` against the canonical `indicators`
schema (`src/features/writer.py`). Migrations 007–010 applied (006 skipped — known
fresh-DB bug). All 6 (symbol×regime_tf) scenarios fetched ~21.5k labeled 1h bars.

**Summary:**
- Window: 2024-01-01 → 2026-06-15
- Base: 1h; Regime TFs: 4h, 1d; Horizon: 6 bars; Fee+slip: 0.10%
- Verdict: NO_PULSE — no (symbol, regime_tf) met the full Gate 1 thresholds.

**Per (symbol:rtf) bucket summary (net % forward, h=6):**

| Scenario | labeled | favorable | unfavorable | Δ net % | sample_ok | sep_ok |
|----------|--------:|----------:|------------:|--------:|:---------:|:------:|
| SOLUSDT:4h | 21,518 | 1,493 | 20,019 | +0.0856 | ✅ | ❌ |
| SOLUSDT:1d | 21,499 | 1,944 | 19,549 | −0.0951 | ✅ | ❌ |
| BTCUSDT:4h | 21,519 | 1,464 | 20,049 | −0.0062 | ✅ | ❌ |
| BTCUSDT:1d | 21,500 | 2,592 | 18,902 | +0.0568 | ✅ | ❌ |
| ETHUSDT:4h | 21,521 | 1,968 | 19,547 | +0.0690 | ✅ | ❌ |
| ETHUSDT:1d | 21,502 | 3,840 | 17,656 | −0.0497 | ✅ | ❌ |

Example detail (SOLUSDT:4h): favorable mean −0.0057% / P(loss) 51.8% vs
unfavorable mean −0.0913% / P(loss) 52.6% — a 0.086% gap, both buckets ~52% loss.

**Gate 1 evaluation:**
- Sample: PASS (≫200 per bucket on every scenario; Gate 0 coverage confirmed on real data).
- Separation: FAIL — every Δ is ≤ 0.096% vs the required ≥ 0.15% (15 bps), and the
  signs are **inconsistent across symbols** (SOL/BTC/ETH disagree at both 4h and 1d).
- Robustness: N/A (no base separation to confirm).
- Result: **NO_PULSE**.

**Read:** The trending/high-vol higher-TF regime label (|ema_slope_50|>0.005 ∧
trend_consistency>60 ∧ volatility_percentile>60) carries no harvestable forward-return
edge for 1h entries net of costs. The favorable regime occurs 7–18% of bars and does
not separate forward returns meaningfully or consistently. Per RBI rules NO_PULSE stops
the lane at the probe gate (no autoresearch). Lane CLOSED.

**Files written:**
- `research/rbi_loop/higher-tf-regime-allocator-v0/probe-verdict.json` (real run).
- This report.

**Post-probe:** Lane stops here. No manifests, no `rbi_loop.*.yaml`, no strategy code,
no `--execute`, no deploy. The A/B fork (allocator-gate vs standalone candidate #2) is
moot — closed at Gate 1.

**Follow-up defects surfaced (handed to builder):**
1. The probe emits NO_PULSE on `labeled=0` (missing tables → caught fetch exception →
   empty result). It must hard-fail when total labeled bars is 0 or any fetch errors,
   so a data gap can never masquerade as a no-edge verdict.
2. Migration `006_normalize_position_market_labels` fails on a fresh DB and blocks
   007–010; the chain does not reproduce the canonical `indicators` schema.
