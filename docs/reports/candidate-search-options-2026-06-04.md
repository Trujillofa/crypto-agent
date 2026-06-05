# Candidate Search Options — June 2026

**Portfolio goal (still open):** 5–10 **independent** agents, each passing standard +
bootstrap=1000 gates. **Current:** 1 deployable technical (`agent_sol_1h_trend_pullback_overlay_live`)
+ `agent_sentiment_macro` (independent on OOS entries).

**Principle:** agent count is subordinate to gates and independence. A correlated
second agent adds risk faster than it adds validation speed.

This document elaborates **what to do next**, with trade-offs, after ~1,440+
autoresearch runs, Wave 7 (4h surfaces), relative-strength probe failure, and
funding-normalization probe failure at default thresholds.

---

## Option A — Phase 0 forward validation (always on)

**What:** Keep live agents running; measure **real** execution and independence from
**attributed** fills (`agent_id` on positions since `bc309ae`).

**Weekly command:**

```bash
./scripts/run_phase0_weekly.sh
# or manually:
./scripts/run_entry_overlap_remote.sh
```

**Track:**

| Milestone | Why |
|-----------|-----|
| 5 closed SOL overlay trades | Execution sanity, frequency |
| 10 closed | Weak forward-quality read |
| 20 closed | Stronger validation read |

**Also watch:** fill/slippage, exchange SL/TP placement, correlated losses with
sentiment-macro on the same SOL regime.

**Pros:** Uses real money path; no research compute; validates the one promoted edge.
**Cons:** Slow (~1–2 trades/month on overlay); does not by itself create candidate #2.
**When to prefer:** Always — this is the floor. Do not scale notional until milestones pass.

**Status:** Active. Live overlap DB rows still **0** — too early for realized overlap;
OOS overlap remains **0** shared entries vs sentiment-macro.

---

## Option B — Funding normalization primary (reshape, not abandon yet)

**What:** Treat funding/crowding as the **primary** trigger when funding **normalizes**
from an extreme, net of funding drag, one entry per cycle.

**Already done:**

- Surface brief: `docs/specs/funding-crowding-primary-surface-v0.md`
- Probe: `scripts/probe_funding_normalization.py`
- DB coverage: BTC/ETH/SOL backfilled
- Default-threshold prod result: **NO_PULSE / SPARSE / negative net** — see
  `funding-normalization-probe-2026-06-04.md`

**Why defaults failed:**

| Symbol | Issue |
|--------|--------|
| BTC/ETH | Never reached −0.05% funding in 2024–2026; long-side extreme is rare |
| SOL | Only 6 normalization events at −0.05%; net forward negative; concentration fail |

**Reshape sub-options (run before any implementation):**

| Sub-option | Command / tool | Hypothesis |
|------------|----------------|------------|
| **B1 Fixed lower entry** | `--entry-threshold 0.00015` | More events; ETH ~12, SOL ~53 — still SPARSE/WEAK_EDGE |
| **B2 Per-symbol negative tail** | `run_funding_probe_reshape.py` scenario `neg_tail_5pct` | Threshold = 5% most negative observed rate per symbol |
| **B3 Short-side normalization (probe only)** | `--include-short-events` / `both_norm_probe` | BTC/ETH crowding is often **positive** funding; shorts deferred for live until parity review |
| **B4 Longer hold windows** | `--forward-bars-24h 48` | Normalization edge may appear slower than 12h |
| **B5 Price confirmation gate** | Add to probe v2: only fire if RSI/EMA agree | Reduces false normalizations in chop |

**Decision rule (unchanged):**

- Implement `funding_normalization_standalone` **only** if some scenario gets
  **HAS_PULSE**: ≥20 long events, positive mean **net** 12h or 24h, concentration ≤30%.

**Pros:** Different primitive from exhausted 1h technical stack; data now exists.
**Cons:** First probe pass weak; may be structurally low edge in 2024–2026 spot bull regime.
**When to prefer:** After reshape matrix shows at least one HAS_PULSE cell.

**Reshape matrix result (prod, 2026-06-04):** Only **SOL `neg_tail_10pct`** hit
`HAS_PULSE` (44 long events, +0.06% mean net 24h; 12h mean still negative).
BTC/ETH best cells stay SPARSE. `both_norm_probe` adds many short events — research
only until short parity review.

```bash
ssh crypto-agent 'cd /opt/crypto-agent && docker run --rm --network crypto-agent_crypto-net \
  -v /opt/crypto-agent:/app -w /app -e PYTHONPATH=/app --env-file /opt/crypto-agent/.env \
  -e POSTGRES_HOST=timescaledb crypto-agent-agent_sentiment_macro:latest \
  python scripts/run_funding_probe_reshape.py'
```

---

## Option C — Relative strength rotation (paused)

**What:** Cross-asset RS vs BTC anchor + controlled pullback.

**Probe result:** 14 events / 20,508 rows, **negative** mean excess vs BTC on ETH/BTC 1h.

**Reshape ideas (only if revisiting):**

- Loosen RS persistence / pullback gates (probe v2)
- SOL target vs BTC anchor (independent from live SOL overlay if entries differ)
- 4h timeframe for fewer, cleaner events

**Pros:** True cross-asset hypothesis.
**Cons:** Failed first probe; implementation cost high (anchor plumbing, 6 components).
**When to prefer:** Only after a **reshaped** RS probe shows positive crude excess and ≥20 events.

**Status:** **Paused** — do not implement v0 spec.

---

## Option D — ETH 4h `range_reversion_bounded` (formal stress only)

**What:** Wave 7 best near-miss: **+13.55% OOS**, 24 WFO trades; fails DD ~20%, P(loss) ~56%.

**Sub-options:**

| Sub-option | Action | Expected outcome |
|------------|--------|------------------|
| **D1 Single b=1000** | One validation run on best overlay archive | Likely reject (like AVAX Wave-2) — documents formal record |
| **D2 Do nothing** | Skip compute | Accept near-miss as research signal only |

**Pros:** Only positive OOS in Wave 7 besides SOL.
**Cons:** Fails promotion pre-filter badly; different symbol (ETH) — independence by symbol, not proven by entries.
**When to prefer:** D1 if you want a paper trail before closing the 4h bounded lane entirely.

---

## Option E — Short-side / two-sided futures (queued)

**What:** Failed-breakdown short, trend short, **positive-funding normalization short**.

**Preconditions (all required):**

- Futures short lifecycle parity in backtest and live
- Risk manager liquidation proximity blocks
- Notifications label shorts clearly
- Paper/shadow agent before live

**Pros:** Portfolio may stay correlated on broad selloffs with only long agents.
**Cons:** Engineering + risk review before any research campaign; funding probe short leg is research-only today.
**When to prefer:** After Phase 0 proves long-side execution; funding reshape B3 may inform whether short normalization has pulse.

---

## Option F — New first-principles brief (if B and C fail)

**What:** Stop incremental tuning; write a **new** spec (like RS and funding), probe first, then implement.

**Candidate ideas (not vetted):**

| Idea | Different primitive | Notes |
|------|---------------------|-------|
| Volatility squeeze breakout bounded | Regime expansion | Complements mean reversion |
| Session / liquidity window router | Time microstructure | Needs session labels |
| Cross-exchange basis / premium | Crowding | Data not in DB today |
| Liquidation cascade proxy | Microstructure | Needs L2 or agg trades |

**Pros:** Avoids repeating failed lanes.
**Cons:** Highest spec + data cost.
**When to prefer:** Funding reshape matrix all fail AND Phase 0 still shows only one edge.

---

## Option G — Merge research/ops PR and freeze sweeps

**What:** Merge `docs/relative-strength-rotation` → `main` ([PR #55](https://github.com/Trujillofa/crypto-trading-agent/pull/55)).

**Contains:** RS probe, funding spec/probe, Phase 0 fixes, forward snapshot, plan index updates.

**Pros:** Single source of truth for humans and agents.
**Cons:** Resolve merge conflicts (futures_executor done on branch).
**When to prefer:** **Now** — before more campaigns.

---

## Recommended priority stack

```text
1. Merge PR #55 (Option G)
2. Continue Option A weekly
3. Run Option B reshape matrix (B1–B3); stop if no HAS_PULSE
4. If B fails: Option F (new brief) OR Option D1 (ETH b=1000 record only)
5. Option E only after execution parity doc exists
6. Option C only if RS probe v2 passes
```

**Do not:** Rerun BNB/BTC 1h standalone, AVAX/ETH #0004, SOL 1h clones, or lower gates.

---

## Quick reference — scripts

| Script | Purpose |
|--------|---------|
| `run_phase0_weekly.sh` | Overlap + optional PnL snapshot |
| `run_entry_overlap_remote.sh` | SOL 1h WFO + live overlap |
| `probe_funding_normalization.py` | Single-symbol funding probe |
| `run_funding_probe_reshape.py` | Multi-scenario reshape matrix |
| `probe_relative_strength_rotation.py` | RS feasibility (paused) |

---

## One-line takeaway

The **5–10 agent goal is not failed** — it needs **independent edge**. Run Phase 0
continuously, merge the research record, reshape funding probes once; if still no
pulse, pick **Option F** (new brief) rather than another 1h parameter sweep.
