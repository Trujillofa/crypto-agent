# RBI Loop Decision — cross-venue-dislocation-event-v1

| Field | Value |
|---|---|
| Generated at | 2026-06-12T20:41:44.054907+00:00 |
| Action | `RUN_AUTORESEARCH` |
| Allowed | True |
| Execute requested | False |
| Selected command | `uv run python scripts/autoresearch_loop.py --config config/settings.autoresearch.yaml --symbol SOLUSDT --timeframe 1h --train-months 3 --test-months 2 --gate-profile standard --families dislocation_event_rolling_entry --max-runs 30` |

## Reasons

- cheap probe passed; autoresearch result missing

## Evidence

| Field | Value |
|---|---|
| lane_brief | docs/specs/cross-venue-dislocation-event-strategy-v1.md |
| probe_verdict | HAS_PULSE |

## Execution

No command execution was recorded.

## Next Action

RUN_AUTORESEARCH
