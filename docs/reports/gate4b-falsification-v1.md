# Gate 4b falsification v1 — run record

**Charter:** `docs/specs/gate4b-falsification-charter-v1.md`
**Code pin:** `73161a8` (charter merge #173)
**Run date:** 2026-08-14
**Study seed:** 42
**Do not retune.** Threshold stays `0.0`.

## Commands

Each frozen command was invoked **once**. Overlay JSON was not produced because
`execution_parity_v2` requires complete 8h funding marks and this TimescaleDB has none
for SOLUSDT.

| Control | Result | Fingerprint |
|---|---|---|
| NC-SOL-OVERLAY-0.80 | abort: `Missing historical funding settlements` | no JSON |
| NC-SOL-OVERLAY-1.27 | abort: same funding gap | no JSON |
| NC-ETH-4H-RANGE | JSON written | `102667f6debc382061619344368ce8fc16ab2f2cbee3eca24bbbf485d237066b` |

Artifacts: `research/gate4b-falsification-v1/`. Raw log: `run.log`.

ETH audit (resolved, not retuned): fee `0.0004`, slip `0.0002`, trend OFF (`cli_override`),
spot, `execution_parity_v2`. Fit `2024-01-01`→`2024-07-01`, `row_count=895`,
`row_fingerprint=2e0f482a023759820528f59e68f625c33d420d15c071b88058bcc55353fa6511`,
`eval_bars_used=480`.

## ETH path outcomes

| Path | Kind | Trades | Outcome |
|---|---|---:|---|
| regime_0 | regime | 16 | **pass** (+4.07%, DD 8.72%) |
| regime_1 | regime | 12 | **pass** (+4.47%, DD 5.35%) |
| regime_2 | regime | 9 | **pass** (+9.08%, DD 4.59%) |
| march_2020_gap | stress | 0 | skip |
| funding_blowout | stress | 11 | fail (−10.39%, DD 12.24%) |
| flat_wide_range | stress | 0 | skip |

`status=inconclusive`. `regime_scored=3`, `stress_scored=1`. Split coverage **not** met
(stress floor is 2). `pass_rate_pct=0.0` is not a scored verdict.

## Interpretation (charter criteria, applied once)

1. Overlay negatives produced **no** scored paths (environment missing funding marks).
   That is lack of coverage, not a synthetic fail.
2. ETH is a known historical reject that **passed all three regime paths** and scored
   only one stress path. Charter rule 1: pass **or** lack of split coverage → Gate 4b
   is not discriminating.

**Decision:** keep `min_synthetic_pass_rate_pct=0.0`. Do not arm a threshold. Reserved
seeds 7/99 and period `2025-07-01`→`2026-02-23` stay unused.

No generators, floors, or commands were changed after looking at JSON.
