# RBI Loop Decision — cross-venue-basis-v1

| Field | Value |
|---|---|
| Generated at | 2026-06-11T21:13:21.842319+00:00 |
| Action | `ITERATE_OR_CLOSE` |
| Allowed | False |
| Execute requested | False |
| Selected command | `` |

## Reasons

- autoresearch result did not pass the standard gate

## Evidence

| Field | Value |
|---|---|
| lane_brief | docs/specs/cross-venue-basis-dislocation-brief-v0.md |
| probe_verdict | HAS_PULSE |
| last_result_status | discard_all |
| gate_profile | standard |
| bootstrap |  |
| passes_standard_gate | False |
| eligible_for_bootstrap_1000 | False |
| promotion_candidate_failures | `["require_mode_trade_starvation", "block_mode_no_risk_improvement", "bootstrap_p_loss_far_above_gate"]` |

## Execution

No command execution was recorded.

## Next Action

ITERATE_OR_CLOSE
