# Funding / Crowding Primary Surface v0

**Status:** design + feasibility probe (no full implementation until probe shows pulse)
**Date:** 2026-06-04
**Goal:** candidate #2+ with **independent** edge versus SOL 1h overlay and sentiment-macro

---

## Why this surface

Prior waves treated funding as one vote in a five-strategy stack or as an extreme-level
trigger. Those lanes produced 0 trades or negative OOS. This surface changes the
question:

> After crowded positioning **unwinds** (funding normalizes from an extreme), is there
> a tradeable mean-reversion edge **net of funding costs**?

Production DB now has `funding_rates` for **BTCUSDT, ETHUSDT, SOLUSDT** (2024-01-01 →
present). Phase 3 research may proceed only after a cheap probe passes.

---

## Hypothesis

**HYP-001:** Entering **long** when funding normalizes from an extreme **negative**
(crowded shorts paying longs) yields positive forward return after netting funding
paid/received during the hold window.

**HYP-002:** Entering on the **extreme level alone** (without normalization) is weaker
or negative — normalization is the primary trigger, not the extreme.

**HYP-003:** Edge must not be dominated by one funding event (concentration cap).

---

## Scope (v0)

| In scope | Out of scope (v0) |
|----------|-------------------|
| BTCUSDT, ETHUSDT, SOLUSDT | BNB, AVAX, alt baskets |
| Futures perpetual funding | New ingestion pipelines |
| Long-only entries first | Live short-side until parity review |
| Normalization-triggered entries | Re-running `funding_extreme_overlay` vote stack |
| One entry per funding cycle | Repeated entries on same unwind |
| Net return after funding drag | Gross-only promotion |

---

## Signal definition

### States (per symbol)

1. **Idle** — funding inside normal band.
2. **Extreme negative** — `funding_rate <= -entry_threshold`.
3. **Extreme positive** — `funding_rate >= +entry_threshold` (tracked; shorts deferred).
4. **Normalization event (long)** — transition from extreme negative to
   `|funding_rate| < exit_threshold` without re-entering extreme negative on the same
   tick.

### Parameters (bounded in autoresearch later)

| Parameter | Default (probe) | Role |
|-----------|-----------------|------|
| `entry_threshold` | 0.0005 (0.05%) | Crowding extreme |
| `exit_threshold` | 0.00015 (0.015%) | Normalized band |
| `cooldown_funding_periods` | 1 | Block re-entry until next extreme cycle |

### Entry rules (long v0)

- Fire **once** per negative extreme → normalization cycle.
- Require liquid symbol; optional price filter (close above EMA200) in full impl only.
- Reject entry if forward window would include a known liquidation cascade proxy
  (full impl: ATR percentile cap).

### Exit / hold (full implementation)

- Time-stop aligned to funding cadence (e.g. 12–24h on 1h chart).
- Exit when funding re-extremes opposite direction or rotation failure.
- Model executor SL/TP parity in backtest.

---

## Funding cost

For each event, compute:

```
gross_forward_pct = (close[t+H] / close[t] - 1) * 100
funding_drag_pct  = sum(funding_rate over payments in (t, t+H]) * 100  # long pays +rate
net_forward_pct   = gross_forward_pct - funding_drag_pct
```

Probe reports **12h and 24h** horizons (1h bars: 12 and 24).

---

## Concentration guard

Reject full implementation if, on probe events with positive net return:

```
max_single_event_net / sum(positive_net) > 0.30
```

Promotion gate (unchanged): profit concentration ≤ 50% on WFO — probe uses a stricter
30% pre-filter.

---

## Feasibility probe (required before code)

```bash
uv run python -m scripts.probe_funding_normalization \
  --symbol ETHUSDT \
  --timeframe 1h \
  --start 2024-01-01T00:00:00 \
  --end 2026-06-01T00:00:00
```

**Kill / reshape if:**

- Normalization events < 20 in window (too sparse for standard gate path).
- Mean **net** forward return ≤ 0 at both 12h and 24h.
- Concentration > 30% on positive net events.

**Proceed to implementation if:**

- Events ≥ 20 and mean net forward > 0 on at least one horizon, with concentration OK.

Probe script: `scripts/probe_funding_normalization.py`

---

## Promotion path (unchanged)

```
b=100 standard discovery
  → promotion_candidate / eligible_for_bootstrap_1000
  → bootstrap=1000 (same standard gate)
  → entry overlap vs SOL overlay + sentiment-macro
  → paper → small live
```

No gate lowering to reach the 5–10 agent portfolio goal.

---

## Implementation checklist (after probe pulse)

1. `funding_normalization_standalone` autoresearch family (not overlay vote).
2. Strategy `funding_normalization` with cycle cooldown and net-cost-aware backtest notes.
3. Fail-fast if `funding_rates` coverage missing for symbol/window.
4. Tests: state machine, no double-entry per cycle, no future funding leakage.
5. First campaign: ETHUSDT 1h, 80 runs, `GATE_PROFILE=standard`.

---

## Related docs

- [`autoresearch-next-candidate-path-2026-06-04.md`](../reports/autoresearch-next-candidate-path-2026-06-04.md)
- [`forward-validation-snapshot-2026-06-04.md`](../reports/forward-validation-snapshot-2026-06-04.md)
- Wave 7 ledger: BTC 4h `funding_primary_standalone` had **0 trades** — different trigger; do not conflate.
