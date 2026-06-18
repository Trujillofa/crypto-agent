# Closed-Family Cost-Corrected Re-Screen — 2026-06-18

**Spec:** [closed-family-cost-corrected-rescreen-v0.md](../specs/closed-family-cost-corrected-rescreen-v0.md)
**Audit:** [backtest-engine-integrity-audit-2026-06-18.md](backtest-engine-integrity-audit-2026-06-18.md)
**Predecessors:** [cost-realism-rerun-2026-06-18.md](cost-realism-rerun-2026-06-18.md), [dislocation-cost-isolation-2026-06-18.md](dislocation-cost-isolation-2026-06-18.md)

## Frozen lane set (pre-registered)

- **sol-4h-rsi-reversal** [RUN] — SOLUSDT 4h rsi_reversal standalone: Production/autoresearch mean-reversion vote on SOL 4h (BASE_STRATEGY_CONFIGS params). Original closure: five-strategy stack 0/704 config-search passes.
- **avax-4h-bollinger-strategy** [RUN] — AVAXUSDT 4h bollinger_bounce standalone: AVAX 4h WFO sweep best-shape config BB_D0.0_RSI30_70_TS720_SL2.0_TP3.0 (docs/reports/avax-wfo-bollinger-20260408-153456.json).
- **avax-4h-mean-reversion** [SKIP] — AVAXUSDT 4h mean_reversion standalone: AVAX 4h WFO best candidate MR_L120_ZE2.0_ZX0.25_TS1440_SL2.5_TP3.0 — positive OOS return but sparse trades.
- **eth-4h-range-reversion-bounded** [RUN] — ETHUSDT 4h range_reversion_bounded (bollinger_bounce): Wave 7 near-miss (+13.55% OOS, 24 WFO trades, Sharpe 0.48). Same overlay as cost-realism rerun #92.

Costs: **main defaults post #94** (fee 0.04%/side, slippage 0.02%/side, `scaled_8h` funding). Only the global trend filter is swept (Cell A OFF, Cell B ON).

## Skipped lanes

### `avax-4h-mean-reversion`

Config not runnable on current harness: MeanReversionStrategy is not registered in settings YAML registry and indicators table lacks pair_close_price required by src/strategy/mean_reversion.py. Per brief: document and skip — no new param search.

**Original legacy verdict (reference):** FAIL — docs/reports/avax-wfo-mean_reversion-20260408-153450.json (legacy costs)
_Failed oos_trades / oos_win_rate gates; pair-spread strategy._

## Lane: `avax-4h-bollinger-strategy`

**Best-of screen:** cell_a (corrected cost + filter OFF) → **FAIL**

| Metric | Cell A (filter OFF) | Cell B (filter ON) | Original (legacy) |
|---|---:|---:|---:|
| wfo_return_pct | 11.14 | -3.45 | -25.27 |
| wfo_sharpe | 0.81 | -0.21 | -1.45 |
| wfo_trades | 26 | 7 | 65 |
| max_drawdown_pct | 22.28 | 7.17 | 60.31 |
| profit_concentration | 69.92 | 100.00 | — |
| verdict | FAIL | FAIL | FAIL |

_Original reference: docs/reports/avax-wfo-bollinger-20260408-153456.json (legacy costs). AVAX WFO oos_* metrics under pre-#94 engine defaults._

### cell_a — resolved cost + filter audit

**Cost audit:**

```json
{
  "name": "corrected",
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "funding_cadence": "scaled_8h",
  "base_futures_funding_rate": 0.0001,
  "round_trip_cost_pct": 0.12000000000000001,
  "funding_method": "scale per-bar rate by tf_hours/8 (equivalent to 8h settlement cadence)",
  "effective_futures_funding_rate": 0.0
}
```

**Global trend filter audit:**

```json
{
  "active": false,
  "buffer_pct": 0.0,
  "source": "cost_profile_override",
  "config_explicit": null
}
```

**BacktestConfig (key fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 0.0001,
  "funding_cadence": "scaled_8h",
  "global_trend_filter_buffer_pct": 0.0
}
```

### cell_b — resolved cost + filter audit

**Cost audit:**

```json
{
  "name": "corrected",
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": true,
  "funding_cadence": "scaled_8h",
  "base_futures_funding_rate": 0.0001,
  "round_trip_cost_pct": 0.12000000000000001,
  "funding_method": "scale per-bar rate by tf_hours/8 (equivalent to 8h settlement cadence)",
  "effective_futures_funding_rate": 0.0
}
```

**Global trend filter audit:**

```json
{
  "active": true,
  "buffer_pct": 0.0,
  "source": "cost_profile_override",
  "config_explicit": null
}
```

**BacktestConfig (key fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": true,
  "futures_mode": false,
  "futures_funding_rate": 0.0001,
  "funding_cadence": "scaled_8h",
  "global_trend_filter_buffer_pct": 0.0
}
```

## Lane: `eth-4h-range-reversion-bounded`

**Best-of screen:** cell_b (corrected cost + filter ON) → **FAIL**

| Metric | Cell A (filter OFF) | Cell B (filter ON) | Original (legacy) |
|---|---:|---:|---:|
| wfo_return_pct | -46.52 | 3.66 | 1.27 |
| wfo_sharpe | -1.84 | -0.29 | -0.71 |
| wfo_trades | 66 | 14 | 15 |
| max_drawdown_pct | 71.53 | 28.06 | 28.69 |
| profit_concentration | 100.00 | 100.00 | 100.00 |
| verdict | FAIL | FAIL | FAIL |

_Original reference: research/cost-realism-rerun/eth-4h-range-reversion-bounded-legacy.json. Ledger Wave 7 NEAR_MISS: +13.55% OOS at b=100 but DD/P(loss)/Sharpe fail._

### cell_a — resolved cost + filter audit

**Cost audit:**

```json
{
  "name": "corrected",
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "funding_cadence": "scaled_8h",
  "base_futures_funding_rate": 0.0001,
  "round_trip_cost_pct": 0.12000000000000001,
  "funding_method": "scale per-bar rate by tf_hours/8 (equivalent to 8h settlement cadence)",
  "effective_futures_funding_rate": 0.0
}
```

**Global trend filter audit:**

```json
{
  "active": false,
  "buffer_pct": 0.0025,
  "source": "cost_profile_override",
  "config_explicit": null
}
```

**BacktestConfig (key fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 0.0001,
  "funding_cadence": "scaled_8h",
  "global_trend_filter_buffer_pct": 0.0025
}
```

### cell_b — resolved cost + filter audit

**Cost audit:**

```json
{
  "name": "corrected",
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": true,
  "funding_cadence": "scaled_8h",
  "base_futures_funding_rate": 0.0001,
  "round_trip_cost_pct": 0.12000000000000001,
  "funding_method": "scale per-bar rate by tf_hours/8 (equivalent to 8h settlement cadence)",
  "effective_futures_funding_rate": 0.0
}
```

**Global trend filter audit:**

```json
{
  "active": true,
  "buffer_pct": 0.0025,
  "source": "cost_profile_override",
  "config_explicit": null
}
```

**BacktestConfig (key fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": true,
  "futures_mode": false,
  "futures_funding_rate": 0.0001,
  "funding_cadence": "scaled_8h",
  "global_trend_filter_buffer_pct": 0.0025
}
```

## Lane: `sol-4h-rsi-reversal`

**Best-of screen:** cell_b (corrected cost + filter ON) → **FAIL**

| Metric | Cell A (filter OFF) | Cell B (filter ON) | Original (legacy) |
|---|---:|---:|---:|
| wfo_return_pct | -4.42 | -4.03 | — |
| wfo_sharpe | 0.09 | 0.41 | — |
| wfo_trades | 120 | 51 | — |
| max_drawdown_pct | 23.97 | 20.46 | 20.66 |
| profit_concentration | 100.00 | 45.96 | — |
| verdict | FAIL | FAIL | FAIL |

_Original reference: docs/reports/current-strategy-review-2026-03-03.md (SOL 4h stack). Standalone legacy WFO not archived; ensemble best-cluster DD ~20.7%, bootstrap P(loss) 48–56%, concentration fail, 0/704 passes._

### cell_a — resolved cost + filter audit

**Cost audit:**

```json
{
  "name": "corrected",
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "funding_cadence": "scaled_8h",
  "base_futures_funding_rate": 0.0001,
  "round_trip_cost_pct": 0.12000000000000001,
  "funding_method": "scale per-bar rate by tf_hours/8 (equivalent to 8h settlement cadence)",
  "effective_futures_funding_rate": 0.0
}
```

**Global trend filter audit:**

```json
{
  "active": false,
  "buffer_pct": 0.0,
  "source": "cost_profile_override",
  "config_explicit": null
}
```

**BacktestConfig (key fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 0.0001,
  "funding_cadence": "scaled_8h",
  "global_trend_filter_buffer_pct": 0.0
}
```

### cell_b — resolved cost + filter audit

**Cost audit:**

```json
{
  "name": "corrected",
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": true,
  "funding_cadence": "scaled_8h",
  "base_futures_funding_rate": 0.0001,
  "round_trip_cost_pct": 0.12000000000000001,
  "funding_method": "scale per-bar rate by tf_hours/8 (equivalent to 8h settlement cadence)",
  "effective_futures_funding_rate": 0.0
}
```

**Global trend filter audit:**

```json
{
  "active": true,
  "buffer_pct": 0.0,
  "source": "cost_profile_override",
  "config_explicit": null
}
```

**BacktestConfig (key fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": true,
  "futures_mode": false,
  "futures_funding_rate": 0.0001,
  "funding_cadence": "scaled_8h",
  "global_trend_filter_buffer_pct": 0.0
}
```

## Decision

**MEAN-REVERSION FAMILY GENUINELY CLOSED** — no runnable lane's best cell passes the standard gate at corrected main defaults under either trend-filter setting. Combined with dislocation isolation (#95), the cost bug hid **no deployable edge** in fee-marginal / trend-filter-confounded families.

**Recommendation:** Stop the structural-probe program and consolidate on sentiment-macro / SOL overlay Phase 0 forward validation.
