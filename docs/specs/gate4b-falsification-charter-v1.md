# Gate 4b negative-control falsification charter v1

**Status:** FROZEN — not executed.
**Date frozen:** 2026-08-14
**Supersedes:** `docs/specs/gate4b-calibration-charter-v0.md` (2026-08-13 INCONCLUSIVE calibration charter).
**Code pin:** `e6a25cc` (`docs(research): mark HYP-HTFR-001 CLOSED NO_PULSE (#172)`). Diagnostic contract from `715c1b0` / #167.
**Do not retune the diagnostic after seeing results.** If this file is edited after a run starts, the run is void.

## Objective

One-sided **falsification** of Gate 4b using known corrected-cost rejects.

This is not threshold calibration. This is not promotion. `min_synthetic_pass_rate_pct` stays `0.0` and off CLI/autoresearch. `--synthetic-diagnostic` must not affect `passes_gates`.

## Why not calibrate

A calibration run needs a defensible **accepted** (positive) control. The former SOL “promoted” overlay was later disarmed after corrected-cost testing (`docs/reports/overlay-threshold-sweep-2026-06-18.md`): no `buy_threshold` both traded and held an edge. Labeling that stack accepted would be dishonest.

Known **negative** controls still let us ask a weaker, useful question: when a resolved config already failed historical gates at corrected costs, does Gate 4b fail it with split coverage?

## Accepted control

**UNSET.** Do not invent one. Overlay `1.07` (same-leg sweep cell) also failed gates and is not a passer. The live `1.27` / `1.07` uptrend split in `config/settings.sol_1h_trend_pullback_overlay_live.yaml` is **not** a sweep cell and is not frozen here.

## Rejected controls (frozen)

Recovered from the named artifacts. SHA-256 is of the file bytes on `e6a25cc`.

### NC-SOL-OVERLAY-0.80

| Field | Frozen value |
|---|---|
| Config | `research/overlay-threshold-sweep/resolved/0.80.yaml` |
| Config SHA-256 | `4fe7874e6a6508b193dd3dff42b257f70e68820dc52a15793da044cf0414b789` |
| Result | `research/overlay-threshold-sweep/0.80.json` |
| Result SHA-256 | `cf9ac986bc1e776151527310a9642c6c763b339165579bd53f9c61d90c79dc42` |
| Market | SOLUSDT 1h, futures, global trend filter ON |
| Binding | `buy_threshold=0.80`, `buy_threshold_uptrend=0.80` |
| Costs | fee `0.0004`, slip `0.0002`, `scaled_8h` funding (`cost_audit.name=corrected`) |
| Historical window | `2024-01-09` → `2026-02-23` |
| Rejection | `passes_gates=false`. 149 / 51 trades. WFO −17.45%. Failures: Sharpe, DD 79.15%, p(loss) 91.2%, OOS, concentration 100%. |
| Report | `docs/reports/overlay-threshold-sweep-2026-06-18.md` |

### NC-SOL-OVERLAY-1.27

| Field | Frozen value |
|---|---|
| Config | `research/overlay-threshold-sweep/resolved/1.27.yaml` |
| Config SHA-256 | `7139c2d2c5a32119bef6d096675da1ff65d925347148afeae2b12bbe428a84f6` |
| Result | `research/overlay-threshold-sweep/1.27.json` |
| Result SHA-256 | `e882a0d97dd0cd3b69d056780913f0191bdddb60ac9c9dee81e1ecd69d0aede8` |
| Market | SOLUSDT 1h, futures, global trend filter ON |
| Binding | `buy_threshold=1.27`, `buy_threshold_uptrend=1.27` (nearest sweep cell to the disarmed live 1.27 gate) |
| Costs | same corrected profile as 0.80 |
| Historical window | `2024-01-09` → `2026-02-23` |
| Rejection | `passes_gates=false`. 24 / 6 trades. WFO +19.42%. Failures: min WFO trades, DD 28.43%, p(loss) 30.4%, concentration 53.95%. Not accepted. |
| Report | `docs/reports/overlay-threshold-sweep-2026-06-18.md` |

### NC-ETH-4H-RANGE

| Field | Frozen value |
|---|---|
| Config | `research/cost-realism-rerun/resolved/eth-4h-range-reversion-bounded.yaml` |
| Config SHA-256 | `b441fea6ea139f7863a09e774178e910b958130e5d3d89d87397a45f02115d11` |
| Result | `research/cost-realism-rerun/eth-4h-range-reversion-bounded-realistic.json` |
| Result SHA-256 | `a3004896739b4d5beb2cc236d4c52ed5a58e8d1081d9d9466dd0f3efa573db29` |
| Market | ETHUSDT 4h, spot, `bollinger_bounce` range-reversion, global trend filter OFF |
| Binding | aggregator `buy_threshold=0.6` / `buy_threshold_uptrend=0.55` (ETH per-symbol 0.6 / −0.58) |
| Costs | fee `0.0004`, slip `0.0002`, `scaled_8h` (`cost_audit.name=realistic`) |
| Historical window | `2024-01-01` → `2026-06-01` |
| Rejection | `passes_gates=false`. 131 / 66 trades. WFO −46.52%. Failures: Sharpe, DD 71.53%, p(loss) 98.0%, OOS, concentration 100%. |
| Report | `docs/reports/cost-realism-rerun-2026-06-18.md` |

## Fit / held-out / reserved

Fit windows are each artifact’s **window-1 train**. They are not derived from synthetic path outcomes.

| Control | Fit (`--synthetic-fit-start` → `--synthetic-fit-end`) | Held-out path start |
|---|---|---|
| NC-SOL-OVERLAY-0.80 | `2024-01-09T00:00:00` → `2024-07-09T00:00:00` | `2024-07-09T00:00:00` |
| NC-SOL-OVERLAY-1.27 | `2024-01-09T00:00:00` → `2024-07-09T00:00:00` | `2024-07-09T00:00:00` |
| NC-ETH-4H-RANGE | `2024-01-01T00:00:00` → `2024-07-01T00:00:00` | `2024-07-01T00:00:00` |

Eval-bar bounds stay in code: `MIN_EVAL_BARS=480`, `MAX_EVAL_BARS=4000`. Record generation timings at both bounds.

| Item | Frozen value |
|---|---|
| Study seed | 42 |
| Regime paths | 3 (`regime_0` … `regime_2`) |
| Stress paths | 3 (`march_2020_gap`, `funding_blowout`, `flat_wide_range`) |
| Regime coverage floor | 2 scored (zero-trade paths leave the denominator) |
| Stress coverage floor | 2 scored (cannot be filled by extra regime paths) |
| Runtime ceiling | 4000 eval bars |
| Reserved seeds (do not use this study) | 7, 99 |
| Reserved period (do not use this study) | `2025-07-01` → `2026-02-23` |

Reserved seeds and the reserved period exist only for a **future** positive-control calibration, after an independent strategy first passes WFO plus `bootstrap=1000`.

## Frozen commands

CLI defaults already apply corrected costs (`fee_rate=0.0004`, `slippage_pct=0.0002`, `funding_cadence=scaled_8h`) when no `CostProfile` is passed. `--execution-profile execution_parity_v2` is the current Gate 4b research default.

Overlay commands must **not** pass `--disable-trend-filter` (artifact trend filter ON). The ETH command **must** pass `--disable-trend-filter` (realistic pass trend filter OFF).

```bash
uv run python scripts/experiment_autopilot.py \
  --config research/overlay-threshold-sweep/resolved/0.80.yaml \
  --symbol SOLUSDT --timeframe 1h \
  --start 2024-01-09 --end 2026-02-23 \
  --seed 42 --bootstrap 500 \
  --execution-profile execution_parity_v2 \
  --synthetic-diagnostic \
  --synthetic-fit-start 2024-01-09T00:00:00 \
  --synthetic-fit-end 2024-07-09T00:00:00 \
  --output-prefix research/gate4b-falsification-v1/nc-sol-overlay-0.80
```

```bash
uv run python scripts/experiment_autopilot.py \
  --config research/overlay-threshold-sweep/resolved/1.27.yaml \
  --symbol SOLUSDT --timeframe 1h \
  --start 2024-01-09 --end 2026-02-23 \
  --seed 42 --bootstrap 500 \
  --execution-profile execution_parity_v2 \
  --synthetic-diagnostic \
  --synthetic-fit-start 2024-01-09T00:00:00 \
  --synthetic-fit-end 2024-07-09T00:00:00 \
  --output-prefix research/gate4b-falsification-v1/nc-sol-overlay-1.27
```

```bash
uv run python scripts/experiment_autopilot.py \
  --config research/cost-realism-rerun/resolved/eth-4h-range-reversion-bounded.yaml \
  --symbol ETHUSDT --timeframe 4h \
  --start 2024-01-01 --end 2026-06-01 \
  --seed 42 --bootstrap 500 \
  --execution-profile execution_parity_v2 \
  --disable-trend-filter \
  --synthetic-diagnostic \
  --synthetic-fit-start 2024-01-01T00:00:00 \
  --synthetic-fit-end 2024-07-01T00:00:00 \
  --output-prefix research/gate4b-falsification-v1/nc-eth-4h-range-reversion
```

Run each command **exactly once** after this charter is committed. Persist the autopilot JSON and markdown. Record `sha256sum` of each JSON as the run fingerprint. Do not rerun. Do not change generators, scoring, floors, seeds, or fit windows after looking at JSON.

## Decision criteria

Interpret strictly. Threshold stays `0.0` after this study.

1. Any negative **passes** synthetic paths, or lacks split coverage (regime scored < 2 or stress scored < 2) → Gate 4b is not discriminating; keep `0.0`.
2. All negatives **fail** with split coverage → useful one-sided evidence; **still** keep `0.0`.
3. Arm a threshold only in a later PR, after a future strategy independently passes WFO plus `bootstrap=1000` **and** then separates from these negatives on the reserved seeds and reserved period.

## Current decision

**FROZEN, not executed.** No diagnostic run in the charter PR. Threshold stays `0.0`. No deployment, no promotion wiring. Independent of XAI PR #171.
