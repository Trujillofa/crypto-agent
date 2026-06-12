# RBI Loop Decision — cross-venue-dislocation-event-v0

| Field | Value |
|---|---|
| Generated at | 2026-06-12T17:27:21.076370+00:00 |
| Action | `RUN_CHEAP_PROBE` |
| Allowed | True |
| Execute requested | True |
| Selected command | `uv run python scripts/probe_dislocation_event_strategy.py --venues binance_usdm,bybit --symbols BTCUSDT,ETHUSDT,SOLUSDT --timeframe 1h --start 2024-01-01 --fee-pct 0.08 --slippage-pct 0.02 --verdict-output research/rbi_loop/cross-venue-dislocation-event-v0/probe-verdict.json` |

## Reasons

- lane brief exists; cheap-probe verdict missing

## Evidence

| Field | Value |
|---|---|
| lane_brief | docs/specs/cross-venue-dislocation-event-strategy-v0.md |
| probe_verdict |  |

## Execution

| Field | Value |
|---|---|
| status | completed |
| returncode | 0 |
| duration_seconds | 115.383 |
| command | uv run python scripts/probe_dislocation_event_strategy.py --venues binance_usdm,bybit --symbols BTCUSDT,ETHUSDT,SOLUSDT --timeframe 1h --start 2024-01-01 --fee-pct 0.08 --slippage-pct 0.02 --verdict-output research/rbi_loop/cross-venue-dislocation-event-v0/probe-verdict.json |
| stdout_tail | `["Dislocation Event Strategy Probe (cross-venue standalone)", "Venues:    binance_usdm, bybit", "Symbols:   BTCUSDT, ETHUSDT, SOLUSDT", "Timeframe: 1h", "Window:    2024-01-01 -> 2026-06-12", "Fee+slip:  0.10%", "Horizons:  (6, 12, 24)", "Threshold: both (rolling_days=90, grid=(3.5, 4.5, 5.5, 7.0))", "Verdict:   HAS_PULSE", "Passing scenarios (Gate 1 met for >=1 horizon/dir in no-lookahead mode):", "  ETHUSDT\|binance_usdm-bybit:extreme_negative:basis_bps:fixed:abs4.5:long:h12", "  ETHUSDT\|binance_usdm-bybit:extreme_negative:basis_bps:fixed:abs4.5:long:h24", "  ETHUSDT\|binance_usdm-bybit:extreme_negative:basis_bps:fixed:abs4.5:long:h6", "  ETHUSDT\|binance_usdm-bybit:extreme_negative:basis_bps:fixed:abs5.5:long:h12", "  ETHUSDT\|binance_usdm-bybit:extreme_negative:basis_bps:fixed:abs5.5:long:h6", "  ETHUSDT\|binance_usdm-bybit:extreme_positive:basis_bps:fixed:abs5.5:long:h24", "  SOLUSDT\|binance_usdm-bybit:extreme_negative:basis_bps:fixed:abs7.0:long:h12", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:basis_bps:fixed:abs4.5:long:h12", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:basis_bps:fixed:abs4.5:long:h24", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:basis_bps:fixed:abs5.5:long:h12", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:basis_bps:fixed:abs5.5:long:h24", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:basis_bps:fixed:abs7.0:long:h24", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:basis_bps:rolling:tail5:long:h12", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:premium_index:rolling:tail10:long:h12", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:premium_index:rolling:tail10:long:h24", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:premium_index:rolling:tail5:long:h24", "  SOLUSDT\|binance_usdm-bybit:extreme_positive:premium_index:rolling:tail5:long:h6", "Wrote guard-consumable verdict to research/rbi_loop/cross-venue-dislocation-event-v0/probe-verdict.json"]` |
| stderr_tail | `[]` |

## Next Action

RUN_CHEAP_PROBE
