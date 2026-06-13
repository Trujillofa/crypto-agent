# RBI Loop Decision — cross-venue-dislocation-event-v1

| Field | Value |
|---|---|
| Generated at | 2026-06-12T22:47:04.943178+00:00 |
| Action | `RUN_AUTORESEARCH` |
| Allowed | True |
| Execute requested | True |
| Selected command | `uv run python scripts/autoresearch_loop.py --config config/settings.autoresearch.yaml --symbol SOLUSDT --timeframe 1h --train-months 3 --test-months 2 --gate-profile standard --families dislocation_event_rolling_entry --max-runs 30` |

## Reasons

- cheap probe passed; autoresearch result missing

## Evidence

| Field | Value |
|---|---|
| lane_brief | docs/specs/cross-venue-dislocation-event-strategy-v1.md |
| probe_verdict | HAS_PULSE |

## Execution

| Field | Value |
|---|---|
| status | completed |
| returncode | 0 |
| duration_seconds | 350.189 |
| command | uv run python scripts/autoresearch_loop.py --config config/settings.autoresearch.yaml --symbol SOLUSDT --timeframe 1h --train-months 3 --test-months 2 --gate-profile standard --families dislocation_event_rolling_entry --max-runs 30 |
| stdout_tail | `["{", "  \\"session_path\\": \\"research/autoresearch_session.json\\",", "  \\"runs\\": 30", "}"]` |
| stderr_tail | `[]` |

## Next Action

RUN_AUTORESEARCH
