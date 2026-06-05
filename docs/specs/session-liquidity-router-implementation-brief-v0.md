# Session Liquidity Router — Implementation Brief v0

**Status:** approved for engineering — **no live deploy until shadow/WFO validation passes**
**Date:** 2026-06-05
**Prerequisite:** `docs/reports/session-liquidity-router-probe-2026-06-05.md` (BTC/ETH/SOL `HAS_PULSE`)
**Surface spec:** `docs/specs/session-liquidity-router-surface-v0.md`

---

## Objective

Implement an **entry gate** that allows consensus **BUY** only during the prod-probe
winning window (`americas`, UTC 16:00–24:00 on 1h bars), converting BUY → HOLD
outside that window. Validate on the **existing SOL 1h trend-pullback overlay** via
backtest A/B and paper shadow **before** any live routing, BTC/ETH campaigns, or
new agent deploy.

This is a **timing/risk filter**, not a new indicator or standalone strategy.

---

## Evidence summary (prod probe)

| Symbol | Baseline 12h | Americas 12h | Baseline MAE | Americas MAE | Verdict |
|--------|--------------|--------------|--------------|--------------|---------|
| BTCUSDT | +0.042% | +0.058% | 1.297% | 1.175% | `HAS_PULSE` |
| ETHUSDT | +0.025% | +0.117% | 1.898% | 1.727% | `HAS_PULSE` |
| SOLUSDT | +0.034% | +0.168% | 2.360% | 2.160% | `HAS_PULSE` |

**v0 router window (fixed from probe — do not tune in v1):**

| Window | UTC `[start, end)` |
|--------|-------------------|
| `americas` | 16 – 24 |

Asia and europe showed no dual improvement vs baseline; do not add multi-window
search in v1 (avoids Wave-10-style retuning).

**Caveat:** Probe measures unconditional bar forwards, not overlay entry quality.
WFO/shadow must prove the gate helps **the promoted stack**, not just the universe.

---

## Hypotheses (testable before live)

| ID | Statement | Falsified if |
|----|-----------|--------------|
| H1 | Americas-only BUY gating **reduces** max DD and/or bootstrap P(loss) on SOL overlay WFO | Risk metrics worse vs ungated baseline |
| H2 | Trade count stays **viable** (WFO trades ≥ 15 on gated run; target ≥ 70% of ungated count) | Sparse collapse like Wave 9 funding |
| H3 | OOS return does not collapse (gated OOS ≥ 50% of ungated OOS, or gated passes `standard`) | Gated OOS deeply negative while ungated was positive |
| H4 | Gate is **orthogonal** to sentiment-macro (overlap unchanged or lower on gated entries) | Gated entries increase overlap vs sentiment-macro |

---

## Scope

| In scope | Out of scope |
|----------|--------------|
| Entry gate on consensus BUY | New entry signal / standalone strategy |
| `americas` window, 1h evaluation timeframe | Sub-hour bars, DST-local “US Eastern” |
| Engine + backtest parity | Live deploy on any agent |
| SOL overlay paper shadow first | Autoresearch `session_router_overlay` until shadow OK |
| BTC/ETH/SOL symbol-agnostic UTC gate | Per-symbol different windows in v1 |
| Long BUY gating only | Short entries until Option E parity |
| SELL / exit signals unchanged | Blocking exits outside window |

---

## Behavior specification

### When gate applies

1. After strategy aggregation produces a consensus signal.
2. **Only if** `final_signal.type == BUY`.
3. **Only if** `session_liquidity_router.enabled == true` in config.
4. Uses **bar timestamp UTC hour** from the indicator row / backtest row `time`
   (same semantics as probe).

### Action

```text
if BUY and not in_allowed_window(utc_hour):
    signal ← HOLD
    reason ← "Blocked by Session Liquidity Router (outside americas 16-24 UTC)"
```

### What does not change

- Open positions: continue to evaluate exits (strategy SELL, executor SL/TP, time stop).
- HOLD and SELL: pass through unchanged.
- Global EMA200 trend filter: remains enabled; session gate runs **after** aggregation,
  **alongside** existing `global_trend_filter` in engine/backtest (order below).

### Filter order (live + backtest)

```text
strategies → aggregator → [SELL flat suppress] → global EMA200 filter → session router → cooldown → emit
```

Mirror this order in `src/strategy/engine.py` and `src/backtest/engine.py` so WFO
matches production.

---

## Configuration (v1 defaults)

Add under `strategy:` in settings YAML (and overlay merge keys):

```yaml
strategy:
  session_liquidity_router:
    enabled: false          # default off — explicit opt-in per agent
    allowed_windows:
      - americas
    # Optional v2; v1 uses probe-fixed disjoint map in code
    block_entries_outside_windows: true
```

Wire in `src/main.py` → `EngineConfig` / `StrategySettings` (names per existing
pattern for `global_trend_filter_*`).

**Paper shadow config:** copy `config/settings.sol_1h_trend_pullback_overlay_paper.yaml`
→ `config/settings.sol_1h_trend_pullback_overlay_paper_americas_gate.yaml` with
`session_liquidity_router.enabled: true` only — no other param changes.

**Do not** enable on `settings.sol_1h_trend_pullback_overlay_live.yaml` until shadow
criteria pass.

---

## Code layout

| # | Task | Location |
|---|------|----------|
| 1 | Pure session helpers (reuse probe logic) | `src/strategy/session_liquidity.py` |
| | `session_for_hour`, `hour_in_windows`, `DEFAULT_WINDOWS` | import from probe or shared module |
| 2 | Deduplicate probe | `scripts/probe_session_liquidity_router.py` imports from `session_liquidity` |
| 3 | Engine gate | `src/strategy/engine.py` after global trend filter block |
| 4 | Backtest gate | `src/backtest/engine.py` same condition on `row["time"]` |
| 5 | Settings parse | `src/main.py` |
| 6 | Unit tests | `tests/test_session_liquidity.py`, extend `tests/test_strategy_engine.py` |
| 7 | Backtest A/B script | `scripts/compare_session_router_backtest.py` (optional CLI) |

**No** `SessionLiquidityRouterStrategy` in the strategy registry — not a vote in the stack.

---

## Validation phases (mandatory order)

### Phase 1 — Unit + engine tests

- UTC hour 15 → block BUY; hour 16 → allow BUY; hour 23 → allow; hour 0 → block.
- `enabled: false` → no blocks.
- SELL unchanged when flat checker passes.

### Phase 2 — Backtest A/B (SOL overlay resolved config)

Run same window, symbol, overlay as promoted agent:

| Run | Config |
|-----|--------|
| A | `settings.sol_1h_trend_pullback_overlay_paper.yaml` (router off) |
| B | `..._paper_americas_gate.yaml` (router on) |

Compare on prod DB date range (2024-01 → 2026-06):

**Wiring guard (required before interpreting metrics):** gated run must report
`blocked_buy_count > 0` in `run_backtest.py` / `BacktestResult` output. If zero,
the A/B comparison is invalid (router not applied) — stop and fix plumbing.

| Metric | Accept direction for B vs A |
|--------|----------------------------|
| WFO trade count | B ≥ 70% of A (not sparse collapse) |
| Max DD | B ≤ A (strict improvement preferred) |
| Bootstrap P(loss) | B ≤ A |
| OOS return | B ≥ 50% of A **or** B passes `standard` |
| Profit concentration | B ≤ A + 10pp (no worse concentration) |

**Phase 2 fail → CLOSED** in ledger; do not enable paper agent or autoresearch.

### Phase 3 — Paper shadow agent (no live)

- New compose service or paper agent id: `sol-1h-trend-pullback-overlay-americas-gate-paper`
- `AGENT_ID` distinct from live overlay
- Run ≥4 weeks parallel to live overlay + Phase 0 weekly
- Track: entries/month, blocked-BUY count, realized PnL vs live overlay

**Phase 3 pass criteria (minimum):**

- Blocked entries logged with router reason (observable in event log / signals)
- No execution anomalies (missed exits, double entries)
- Paper PnL not materially worse than ungated paper over same calendar window

### Phase 4 — WFO / autoresearch (only if Phase 2 pass)

- Family `session_router_overlay` optional: same overlay stacks with
  `session_liquidity_router.enabled: true` and `allowed_windows: [americas]` only.
- **40-run discovery max** on SOL 1h first; BTC/ETH only if SOL gated run improves risk.
- Gates: unchanged `standard` → `promotion_candidate` → b=1000 → overlap.

### Phase 5 — Live (only if Phase 3 + overlap + human approval)

- Never merge gate into live overlay without explicit promote decision.
- Entry overlap vs sentiment-macro required again (gated entries may shift timing).

---

## Stop conditions

| Event | Action |
|-------|--------|
| Phase 2: WFO trades &lt; 70% of baseline | REJECT — window too restrictive for overlay |
| Phase 2: DD/P(loss) worse | REJECT — gate hurts risk |
| Phase 2: OOS collapse | REJECT — do not paper |
| Phase 3: execution bugs | Fix or abandon |
| Autoresearch 0/40 pass | REJECT router lane; keep Phase 0 on ungated live only |
| “Retune” window hours | **Forbidden** in v1 — one reshape max in v2 with new probe doc |

---

## Success criteria (research)

**Engineering done:** gate in engine + backtest, tests green, paper config exists.

**Research success:** Phase 2 shows risk improvement with viable trade count; Phase 3
paper shadow clean; optional autoresearch pass with overlap OK.

**Not required for v1:** new deployable agent count; router may only improve existing
live overlay via config promotion later.

---

## Independence and portfolio notes

- Router on SOL overlay **changes entry timing** but same signal stack — overlap vs
  sentiment-macro must be re-run on **gated** entry series before any live merge.
- BTC/ETH probe pulse supports **symbol-agnostic** UTC gate — good for future BTC/ETH
  agents, but do not launch BTC/ETH campaigns until SOL overlay A/B passes.
- Continue **Phase 0 weekly** on current **ungated** live agents throughout validation.

---

## Engineering checklist

- [ ] `src/strategy/session_liquidity.py` + tests
- [ ] Engine + backtest BUY gate
- [ ] `main.py` config parsing
- [ ] `settings.sol_1h_trend_pullback_overlay_paper_americas_gate.yaml`
- [ ] Phase 2 A/B script or documented `run_backtest.py` commands
- [ ] Ledger row after Phase 2 (PASS / REJECT)
- [ ] Probe import refactor (dedupe `DEFAULT_WINDOWS`)

**Explicitly not in v1 checklist:**

- Live compose change
- `session_router_overlay` autoresearch family
- Prometheus / Telegram (use existing signal logs)

---

## References

- Probe report: `docs/reports/session-liquidity-router-probe-2026-06-05.md`
- Probe script: `scripts/probe_session_liquidity_router.py`
- Live overlay: `config/settings.sol_1h_trend_pullback_overlay_live.yaml`
- Engine trend filter precedent: `src/strategy/engine.py` (global EMA200 block)
- Wave 10 lesson: cheap probe ≠ WFO — Phase 2 required before any campaign
