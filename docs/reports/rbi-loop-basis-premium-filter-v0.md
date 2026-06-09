# RBI Loop Decision — basis-premium-filter-v0

| Field | Value |
|---|---|
| Generated at | 2026-06-09T16:37:11.233960+00:00 |
| Action | `ITERATE_OR_CLOSE` |
| Allowed | False |
| Execute requested | False |
| Selected command | `` |

## Reasons

- autoresearch result did not pass the standard gate

## Evidence

| Field | Value |
|---|---|
| lane_brief | docs/specs/basis-premium-risk-filter-surface-v0.md |
| probe_verdict | HAS_PULSE |
| last_result_status | keep |
| gate_profile | filter_wfo_ab |
| bootstrap |  |
| passes_standard_gate | False |
| eligible_for_bootstrap_1000 | False |
| promotion_candidate_failures | `["insufficient_blocks", "no_risk_improvement", "oos_return_not_better"]` |

## Execution

No command execution was recorded.

## Next Action

ITERATE_OR_CLOSE
