# Cost-Realism Re-Run Report — 2026-06-18

**Spec:** [cost-realism-rerun-brief-v0.md](../specs/cost-realism-rerun-brief-v0.md)
**Audit:** [backtest-engine-integrity-audit-2026-06-18.md](backtest-engine-integrity-audit-2026-06-18.md)

## Frozen lane set (pre-registered)

- **daily-trend-long-btc** — daily-trend-long SMA50 BTCUSDT 1d: Primary HAS_PULSE→Gate-2-FAIL lane from daily-trend-long-gate2.md; SMA50 on BTC (probe baseline symbol).
- **daily-trend-long-eth** — daily-trend-long SMA50 ETHUSDT 1d: Same lane family on ETH (positive OOS return under legacy gate2, still FAIL).
- **daily-trend-long-sol** — daily-trend-long SMA50 SOLUSDT 1d: Same lane family on SOL (weakest symbol in gate2 report).
- **eth-4h-range-reversion-bounded** — ETHUSDT 4h bollinger_bounce range_reversion_bounded: Mean-reversion dip-buy lane (Wave 7 near-miss +13.55% OOS, Sharpe 0.48). Buys lower-band touches — structurally blocked when global EMA200 filter is on.
- **sol-1h-dislocation-event** — SOLUSDT 1h dislocation_event rolling basis_spread tail5 h24: Fee-marginal lane: Gate-1 HAS_PULSE at 0.08%+0.02% probe cost; v1 sweep best shape still negative at legacy ~0.4% round-trip engine defaults.

## Funding cadence method

Realistic pass uses **scaled per-bar funding**: `effective_futures_funding_rate = base_rate × (timeframe_hours / 8)`. This is equivalent to charging the full 8h rate only on bars that represent one 8h funding period, without editing `_apply_funding`. Legacy pass keeps per-bar `0.0001` (engine default). All three frozen lanes run spot (`futures.enabled: false`), so funding does not affect these results; the scaling is wired for futures lanes in future reruns.

## Lane: `daily-trend-long-btc`

| Metric | Legacy | Realistic | Δ (realistic − legacy) |
|---|---:|---:|---:|
| total_return_pct | 51.04 | 49.90 | -1.14 |
| wfo_return_pct | -7.29 | -15.53 | -8.24 |
| wfo_sharpe | -0.40 | -0.81 | -0.41 |
| wfo_trades | 9 | 13 | +4 |
| max_drawdown_pct | 19.79 | 24.87 | +5.07 |
| profit_concentration | 100.00 | 100.00 | +0.00 |
| gate_verdict | FAIL | FAIL | — |

**Resolved cost audit (realistic pass):**

```json
{
  "name": "realistic",
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

**Resolved BacktestConfig (realistic pass, key cost fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 0.00030000000000000003,
  "global_trend_filter_buffer_pct": 0.0
}
```

## Lane: `daily-trend-long-eth`

| Metric | Legacy | Realistic | Δ (realistic − legacy) |
|---|---:|---:|---:|
| total_return_pct | 27.73 | 89.52 | +61.78 |
| wfo_return_pct | 29.92 | 23.88 | -6.04 |
| wfo_sharpe | -0.31 | -0.41 | -0.10 |
| wfo_trades | 4 | 7 | +3 |
| max_drawdown_pct | 34.27 | 29.29 | -4.98 |
| profit_concentration | 100.00 | 100.00 | +0.00 |
| gate_verdict | FAIL | FAIL | — |

**Resolved cost audit (realistic pass):**

```json
{
  "name": "realistic",
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

**Resolved BacktestConfig (realistic pass, key cost fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 0.00030000000000000003,
  "global_trend_filter_buffer_pct": 0.0
}
```

## Lane: `daily-trend-long-sol`

| Metric | Legacy | Realistic | Δ (realistic − legacy) |
|---|---:|---:|---:|
| total_return_pct | -11.13 | -9.58 | +1.55 |
| wfo_return_pct | -3.46 | -11.22 | -7.75 |
| wfo_sharpe | 0.00 | -0.28 | -0.28 |
| wfo_trades | 7 | 10 | +3 |
| max_drawdown_pct | 44.07 | 56.51 | +12.44 |
| profit_concentration | 100.00 | 92.42 | -7.58 |
| gate_verdict | FAIL | FAIL | — |

**Resolved cost audit (realistic pass):**

```json
{
  "name": "realistic",
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

**Resolved BacktestConfig (realistic pass, key cost fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 0.00030000000000000003,
  "global_trend_filter_buffer_pct": 0.0
}
```

## Lane: `eth-4h-range-reversion-bounded`

| Metric | Legacy | Realistic | Δ (realistic − legacy) |
|---|---:|---:|---:|
| total_return_pct | -18.71 | -65.88 | -47.18 |
| wfo_return_pct | 1.27 | -46.52 | -47.79 |
| wfo_sharpe | -0.71 | -1.84 | -1.13 |
| wfo_trades | 15 | 66 | +51 |
| max_drawdown_pct | 28.69 | 71.53 | +42.84 |
| profit_concentration | 100.00 | 100.00 | +0.00 |
| gate_verdict | FAIL | FAIL | — |

**Resolved cost audit (realistic pass):**

```json
{
  "name": "realistic",
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

**Resolved BacktestConfig (realistic pass, key cost fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 5e-05,
  "global_trend_filter_buffer_pct": 0.0025
}
```

## Lane: `sol-1h-dislocation-event`

| Metric | Legacy | Realistic | Δ (realistic − legacy) |
|---|---:|---:|---:|
| total_return_pct | -17.36 | 114.27 | +131.62 |
| wfo_return_pct | -23.76 | 4.64 | +28.40 |
| wfo_sharpe | -0.73 | 0.15 | +0.88 |
| wfo_trades | 91 | 205 | +114 |
| max_drawdown_pct | 45.79 | 43.04 | -2.76 |
| profit_concentration | 100.00 | 79.14 | -20.86 |
| gate_verdict | FAIL | FAIL | — |

**Resolved cost audit (realistic pass):**

```json
{
  "name": "realistic",
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

**Resolved BacktestConfig (realistic pass, key cost fields):**

```json
{
  "fee_rate": 0.0004,
  "slippage_pct": 0.0002,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 1.25e-05,
  "global_trend_filter_buffer_pct": 0.0
}
```

## Read-out

**VERDICT FLIP (materially closer)** — 2 lane(s) remain FAIL but move materially closer under realistic costs: daily-trend-long-eth, sol-1h-dislocation-event.

Strongest signal: `sol-1h-dislocation-event` WFO return −23.8% → +4.6%, Sharpe −0.73 → +0.15 (still below 0.5 gate). `eth-4h-range-reversion-bounded` worsened (−46.5% WFO) when trend filter was removed — suppression was not the binding constraint there.

Implication per brief: tooling was suppressing edges in at least one lane. Escalate to fixing engine defaults (realistic costs, 8h funding, trend-filter opt-in), then re-open fee-marginal / dislocation families before mean-reversion.

**No lane achieved full gate PASS.** Daily-trend-long (all symbols) remains FAIL under both passes.
