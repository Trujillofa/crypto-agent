# Dislocation Cost-Only Isolation — 2026-06-18

**Spec:** [dislocation-cost-isolation-brief-v0.md](../specs/dislocation-cost-isolation-brief-v0.md)
**Prior run:** [cost-realism-rerun-2026-06-18.md](cost-realism-rerun-2026-06-18.md) (PR #92 — bundled cost + filter change)

## Lane

`sol-1h-dislocation-event` — SOLUSDT 1h dislocation_event rolling basis_spread tail5 h24. Same gate profile, period, and params as PR #92. Spot lane (funding cadence irrelevant).

## 2×2 factorial results

| Cell | Cost | Filter | total_return_pct | wfo_sharpe | wfo_trades | max_drawdown_pct | profit_concentration | verdict |
|---|---|---|---:|---:|---:|---:|---:|---|
| cell1 | legacy (0.4% RT) | ON | -17.36 | -0.73 | 91 | 45.79 | 100.00 | FAIL |
| cell2 | realistic (0.12% RT) | ON | 23.15 | -0.22 | 91 | 28.03 | 95.93 | FAIL |
| cell3 | legacy (0.4% RT) | OFF | -10.39 | -0.51 | 205 | 48.32 | 86.09 | FAIL |
| cell4 | realistic (0.12% RT) | OFF | 114.27 | 0.15 | 205 | 43.04 | 79.14 | FAIL |

## Resolved config per cell

### cell1

**Cost audit:**

```json
{
  "name": "legacy",
  "fee_rate": 0.001,
  "slippage_pct": 0.001,
  "apply_global_trend_filter": true,
  "funding_cadence": "per_bar",
  "base_futures_funding_rate": 0.0001,
  "round_trip_cost_pct": 0.4,
  "funding_method": "charge base rate every bar (legacy engine behavior)",
  "effective_futures_funding_rate": 0.0
}
```

**BacktestConfig (key fields):**

```json
{
  "fee_rate": 0.001,
  "slippage_pct": 0.001,
  "apply_global_trend_filter": true,
  "futures_mode": false,
  "futures_funding_rate": 0.0001,
  "funding_cadence": "per_bar",
  "global_trend_filter_buffer_pct": 0.0
}
```

### cell2

**Cost audit:**

```json
{
  "name": "realistic",
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

### cell3

**Cost audit:**

```json
{
  "name": "legacy",
  "fee_rate": 0.001,
  "slippage_pct": 0.001,
  "apply_global_trend_filter": false,
  "funding_cadence": "per_bar",
  "base_futures_funding_rate": 0.0001,
  "round_trip_cost_pct": 0.4,
  "funding_method": "charge base rate every bar (legacy engine behavior)",
  "effective_futures_funding_rate": 0.0
}
```

**BacktestConfig (key fields):**

```json
{
  "fee_rate": 0.001,
  "slippage_pct": 0.001,
  "apply_global_trend_filter": false,
  "futures_mode": false,
  "futures_funding_rate": 0.0001,
  "funding_cadence": "per_bar",
  "global_trend_filter_buffer_pct": 0.0
}
```

### cell4

**Cost audit:**

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

## Sanity check vs PR #92

- cell1 wfo_return_pct: -23.76 vs PR #92 -23.76 — **OK**
- cell1 wfo_sharpe: -0.73 vs PR #92 -0.73 — **OK**
- cell4 wfo_return_pct: 4.64 vs PR #92 4.64 — **OK**
- cell4 wfo_sharpe: 0.15 vs PR #92 0.15 — **OK**

**Sanity:** Cells 1 & 4 reproduce PR #92 within noise.

## Attribution

**Primary driver:** both

| Effect | wfo_return_pct Δ | wfo_sharpe Δ |
|---|---:|---:|
| cost-Δ (Cell2 − Cell1) | +21.56 | +0.51 |
| filter-Δ (Cell3 − Cell1) | -15.26 | +0.21 |

### Recommendation

Both knobs contribute via **interaction**, not a single main effect. Cost-Δ (Cell2−Cell1) improves WFO return by +21.6% and Sharpe by +0.51; filter-Δ (Cell3−Cell1) moves return -15.3% / Sharpe +0.21. Neither cell alone reaches positive WFO return (Cell2 −2.2%, Cell3 −39.0%) — the PR #92 flip requires **both** realistic costs and filter OFF (Cell4 +4.6%). Removing the filter under legacy costs is actively harmful (more trades at 0.4% RT). **Do not re-open** the dislocation/fee-marginal family: best cell still FAIL (WFO Sharpe 0.15 < 0.5 gate, concentration 79% > 50%).
