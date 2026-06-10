# RBI Loop Decision — cross-venue-basis-v1

| Field | Value |
|---|---|
| Generated at | 2026-06-10T15:20:27.900672+00:00 |
| Action | `RUN_AUTORESEARCH` |
| Allowed | True |
| Execute requested | False |
| Selected command | `uv run python scripts/autoresearch_loop.py --config config/settings.autoresearch.yaml --symbol SOLUSDT --timeframe 1h --train-months 3 --test-months 2 --gate-profile standard --families cross_venue_dislocation,venue_basis_filter --max-runs 30` |

## Reasons

- cheap probe passed; autoresearch result missing

## Evidence

| Field | Value |
|---|---|
| lane_brief | docs/specs/cross-venue-basis-dislocation-brief-v0.md |
| probe_verdict | HAS_PULSE |

## Execution

No command execution was recorded.

## Next Action

RUN_AUTORESEARCH
