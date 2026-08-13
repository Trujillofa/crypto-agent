# Gate 4b calibration charter v0

**Status:** INCONCLUSIVE — not executed.
**Date frozen:** 2026-08-13
**Code pin:** `715c1b0` (`feat(backtest): add Gate 4b diagnostic evidence contract (#167)`)
**Do not retune the diagnostic after seeing results.** If this file is edited after a run starts, the run is void.

## Why this is INCONCLUSIVE

A calibration run requires a **defensible accepted (positive) control** and a **rejected control**, named before any diagnostic is executed. No accepted control is nominated here. Inventing one (for example treating the disarmed overlay-live stack as “should pass”) would bake a preference into the threshold. The charter therefore records the freeze rules and stops. Do not invent a control. Do not run `--synthetic-diagnostic` against an improvised pair.

## Freeze rules (must be filled before any run)

| Item | Frozen value | Notes |
|---|---|---|
| Accepted control | **UNSET** | Must be a named config that historically passed WFO/bootstrap for a first-principles reason, not because we need a passer. |
| Rejected control | **UNSET** | Must be a named config already rejected on historical gates or live evidence. |
| Fit period | **UNSET** | Explicit `--synthetic-fit-start` / `--synthetic-fit-end`. Training only. Not derived after seeing path outcomes. |
| Held-out period | **UNSET** | Path generation `start`/eval window must not overlap the fit period. |
| Seed | 42 | Default autopilot seed. Change only in this table before a run. |
| Regime path count | 3 | `regime_0` … `regime_2` |
| Stress path count | 3 | `march_2020_gap`, `funding_blowout`, `flat_wide_range` |
| Regime coverage floor | 2 scored | Zero-trade paths leave the denominator. |
| Stress coverage floor | 2 scored | Cannot be satisfied by extra regime paths. |
| Runtime ceiling | 4000 eval bars | `MIN_EVAL_BARS=480`, `MAX_EVAL_BARS=4000`. Generation timings are recorded at both bounds. |
| Threshold-selection rule | **NOT ARMED** | Only after held-out: accepted control consistently outperforms rejected on **both** classes (regime return floor and stress drawdown). Then a third PR may wire a threshold. If coverage fails or separation fails, keep `min_synthetic_pass_rate_pct=0.0` and keep Gate 4b diagnostic-only. |

## Execution rule

```text
1. Fill UNSET rows in this file and commit the fill *before* running.
2. Run unchanged code at the pin above:
   --synthetic-diagnostic --synthetic-fit-start … --synthetic-fit-end …
   on both controls, same seed and path counts.
3. Do not change generators, scoring, or floors after looking at JSON.
4. Decision is only: (a) threshold PR or (b) remain diagnostic at 0.0.
```

## Current decision

**INCONCLUSIVE.** No accepted control. No diagnostic run. Gate 4b stays diagnostic; threshold stays `0.0` and off CLI/autoresearch.

Follow-ups still later: `requirements.txt` lock cleanup, then news-surprise scoring. Meta-allocator remains blocked on `HYP-HTFR-001` `HAS_PULSE`.
