# Range-Break Continuation Probe — 2026-06-07

**Verdict:** **WEAK_EDGE**
**Script:** `scripts/probe_range_break_continuation.py`
**Spec:** [range-break-continuation-probe-v0.md](../specs/range-break-continuation-probe-v0.md)
**Prerequisite:** [research-reset-2026-06-06.md](./research-reset-2026-06-06.md) — liquidity sweep (mean-reversion after 1h sweeps) CLOSED WEAK_EDGE + explicitly banned.

---

## Run Environment

| Field | Value |
|-------|-------|
| Host | Hetzner `crypto-agent` (prod TimescaleDB via docker) |
| Branch / Worktree | `feat/range-break-continuation-probe` (worktree `crypto-agent-range-break-continuation`) |
| Date executed | 2026-06-06 (post liquidity merge + probe scaffold) |
| Symbols | BTCUSDT, ETHUSDT, SOLUSDT |
| Timeframe | 1h |
| Window | 2024-01-01T00:00:00 → 2026-06-01T00:00:00 |
| Lookback | 24 bars (prior range) |
| Expansion gates | range ×1.2 + volume ×1.2 |
| Fee drag | 0.08% round-trip |
| Min events (per side/symbol gate) | 20 |
| Forward edge gate | net mean > 0.15% on 6h/12h/24h (any) |
| MAE gate | ≥10% improvement vs baseline (any horizon) |
| Conc / month | ≤50% / ≤40% |
| Command | `docker run ... python scripts/probe_range_break_continuation.py` (volume-mounted branch checkout) |

---

## Event Definition (confirmed structural break, opposite of banned sweep-reject)

- **Long (upside break continuation):** `high[t] > max(high[t-24:t])` **AND** `close[t] > max(high[t-24:t])` (closes *outside*, confirmed break not rejected) + range/volume expansion.
- **Short (downside break continuation):** `low[t] < min(low[t-24:t])` **AND** `close[t] < min(low[t-24:t])` + expansions.
- Trade *with* the break. This is the *opposite* surface from `probe_liquidity_sweep.py` (which required close back *inside* the range after the sweep for mean-reversion fade).

See spec for full metrics (forward = signed return in the break direction; MAE = adverse excursion against the position; baseline = all-bar random entry MAE).

---

## Results (from Hetzner execution)

**Verdict:** **WEAK_EDGE**

### Event counts (continuation breaks)

| Symbol | Long (upside break cont.) | Short (downside break cont.) |
|--------|---------------------------|------------------------------|
| BTCUSDT | 641 | 598 |
| ETHUSDT | 621 | 588 |
| SOLUSDT | 590 | 604 |

~600 events per side per symbol — dense (far above 20/80 thresholds). The "close outside" filter is selective vs raw sweeps but still yields hundreds of confirmed breaks over 2.5y.

### Per-side summary (logged 12h metrics + gate eval)

| Scenario | Events | Fwd 12h net (after fee) | MAE imp 12h | Conc | Month | Pass (fwd+MAE+conc+month) |
|----------|--------|---------------------------|-------------|------|-------|---------------------------|
| BTCUSDT:long_break_continuation | 641 | -0.032 | 1.6% | True | True | False |
| BTCUSDT:short_break_continuation | 598 | -0.062 | -12.7% | True | True | False |
| ETHUSDT:long_break_continuation | 621 | 0.122 | 7.4% | True | True | False |
| ETHUSDT:short_break_continuation | 588 | -0.012 | -14.9% | True | True | False |
| SOLUSDT:long_break_continuation | 590 | 0.064 | 5.3% | True | True | False |
| SOLUSDT:short_break_continuation | 604 | -0.140 | -9.5% | True | True | False |

- **Forward edge:** Best observed ~0.122% net on ETH long 12h (still < 0.15% gate). Most sides negative drift after the confirmed break. No horizon combination cleared the "fwd_passes" gate on any symbol/side.
- **MAE control:** Max improvement 7.4% (ETH long) — well below 10% gate. Several sides show *worse* MAE than baseline random entries (larger pullbacks against the position after the "confirmed" break). No side clears mae_passes.
- **Concentration / month:** All pass (events not dominated by single trades or single months; well distributed).
- **Cross-symbol consistency:** Raw mean fwds mixed in sign (BTC negative on both sides; ETH long mildly positive; SOL long positive/short negative). Would have triggered symbols_contradict on qualifying events, but no side qualified the forward gate so moot.

### Full script-rendered report (captured)

```
# Range-Break Continuation Probe — Report

**Verdict:** **WEAK_EDGE**
**Script:** `scripts/probe_range_break_continuation.py`
**Spec:** [range-break-continuation-probe-v0.md](../specs/range-break-continuation-probe-v0.md)

## Config
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT
- Timeframe: 1h
- Window: 2024-01-01T00:00:00 → 2026-06-01T00:00:00
- Lookback: 24 bars
- Fee drag: 0.08% round-trip

## Event counts (continuation breaks)

| Symbol | Long (upside break cont.) | Short (downside break cont.) |
|--------|---------------------------|------------------------------|
| BTCUSDT | 641 | 598 |
| ETHUSDT | 621 | 588 |
| SOLUSDT | 590 | 604 |

## Gate summary (per side)

| Scenario | Events | Fwd>0.15% net | MAE≥10% better | Conc≤50% | Month≤40% | Pass |
|----------|--------|---------------|------------------|----------|-----------|------|
| BTCUSDT:long_break_continuation | 641 | False | False | True | True | False |
| BTCUSDT:short_break_continuation | 598 | False | False | True | True | False |
| ETHUSDT:long_break_continuation | 621 | False | False | True | True | False |
| ETHUSDT:short_break_continuation | 588 | False | False | True | True | False |
| SOLUSDT:long_break_continuation | 590 | False | False | True | True | False |
| SOLUSDT:short_break_continuation | 604 | False | False | True | True | False |

## Passing scenarios
(none)

**Overall verdict:** WEAK_EDGE

See research-reset-2026-06-06.md for banned surfaces and next-lane rules.
```

---

## Interpretation & Failure Mode

- Confirmed range-break continuation (close *outside* prior N-bar extreme + expansion) on 1h BTC/ETH/SOL produces **no tradable forward edge** after realistic fees. Best gross drift before fee is marginal (~0.20% on ETH long 12h implied) and does not survive the 0.08% round-trip + the 0.15% min gate.
- **MAE is not controlled** — in fact often *worse* than a random entry. The "breakout confirmation" (close outside) does not reduce the size of adverse excursions relative to baseline. Price frequently pulls back more after the structural print than a typical bar.
- Event density is healthy and temporal/symbol distribution clean (no concentration red flags). The problem is not "too few samples" or "one weird month" — it is simply that the surface has near-zero or negative expectancy in the forward windows.
- This is the *opposite* result from what the post-liquidity-sweep observations hinted at in other contexts. On 1h majors the "with the confirmed break" primitive does not carry reliably.

Cross-symbol signs are inconsistent anyway, reinforcing that there is no general "break continuation" premium to harvest across the majors with this definition.

---

## Decision

**WEAK_EDGE** (dense events, clean distribution, but core gates fail: no forward edge after fees *and* no MAE improvement).

Per classification table:

- Not HAS_PULSE → do **not** write surface brief.
- Failure mode is clear (flat/negative drift + uncontrolled or larger MAE after confirmed 1h breaks).
- **Close lane.** Record negative result. Do not proceed to strategy, autoresearch, overlay, or any SOL attachment.

Fold into research ledger / reset: "1h range-break continuation (close-outside) has insufficient edge + MAE control on BTC/ETH/SOL. Banned surfaces now include both fade-sweep and ride-break at this granularity/timeframe/symbol set."

No further work on this primitive in this branch. The question is answered: **no**.

(Only if a future probe with different params — e.g. higher TF, different lookback, MTF confirmation, or additional filters — shows clear HAS_PULSE would we revisit.)

---

## Repro / Verification

- Local: `uv run --extra dev ruff check ... && uv run pytest tests/test_range_break_continuation_probe.py -q -v && uv run --extra dev ruff format --check ...` (all pass)
- Hetzner execution used the exact docker invocation in the plan (sentiment_macro image + volume mount of the branch checkout + DB env overrides).
- Exit code from script: 1 (non-zero because not HAS_PULSE), as designed.

All per guardrails: no live configs, no docker-compose, no executor, no paper services, no autoresearch, no SOL settings touched. This branch only produced the cheap-probe answer.
