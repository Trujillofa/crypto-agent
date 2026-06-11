# RBI Loop Decision — cross-venue-dislocation-event-v0

| Field | Value |
|---|---|
| Generated at | 2026-06-11T22:43:29.440942+00:00 |
| Action | `RUN_CHEAP_PROBE` |
| Allowed | True |
| Execute requested | False |
| Selected command | `uv run python scripts/probe_dislocation_event_strategy.py --venues binance_usdm,bybit --symbols BTCUSDT,ETHUSDT,SOLUSDT --timeframe 1h --start 2024-01-01 --fee-pct 0.08 --slippage-pct 0.02 --verdict-output research/rbi_loop/cross-venue-dislocation-event-v0/probe-verdict.json` |

## Reasons

- lane brief exists; cheap-probe verdict missing

## Evidence

| Field | Value |
|---|---|
| lane_brief | docs/specs/cross-venue-dislocation-event-strategy-v0.md |
| probe_verdict |  |

## Execution

No command execution was recorded.

## Next Action

RUN_CHEAP_PROBE
