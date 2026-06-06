# Short-Side Execution / Backtest Parity Audit — v0

**Status:** **IN PROGRESS** — P0 backtest executor-exit parity landed; runtime gaps remain
**Date:** 2026-06-05
**Trigger:** SOL overlay filter/router lanes closed; portfolio is long-biased and needs
independent directionality before more long-only agents.
**Prerequisite:** Phase 0 forward validation continues on live agents (unchanged).

---

## Executive Verdict

| Layer | Short research safe today? | Notes |
|-------|--------------------------|-------|
| **Live futures executor** | **No** | Explicit LONG-only MVP; inverted SL/TP/PnL missing |
| **Strategy engine (runtime)** | **No** | SELL-from-flat suppressed before executor |
| **Paper executor** | **Partial** | Full short path exists behind `allow_short_entry=True`; not wired from `main.py` |
| **Backtest engine** | **Partial** | Executor ATR SL/TP/trailing parity for shorts (P0 done); funding sign still long-biased |
| **Portfolio / risk models** | **Mostly yes** | PnL, liquidation math SHORT-aware |
| **Telegram / reporting** | **No** | BUY/SELL labels only; short entry indistinguishable from long close |

**Conclusion:** Short **WFO** is unblocked after backtest executor-exit parity (P0
backtest, done). Short **paper shadow** requires engine + paper wiring (Step 2).
Short **live/testnet** requires futures executor MVP and reporting fixes. Do not
conflate these gates — cheap short probes must not wait on live executor work.

---

## Context — Why This Audit Now

The promoted SOL 1h overlay is **not improved by external filters**. Session router,
basis premium, funding normalization, vol squeeze, and related overlay ideas all
failed WFO when attached to the same stack. That stack should be treated as a
**standalone live candidate under forward validation**, not a base layer to keep
patching.

Next research must be a **new first-principles surface**. Before short-side crowding
or cross-venue basis probes, the system must prove it can **research and paper**
shorts without hidden mismatch vs longs.

---

## Operating Constraints (unchanged)

1. **Phase 0 mandatory** — track weekly closed trades, PnL, entries/month, SL/TP,
   overlap with `agent_sentiment_macro`, fill quality. Milestones: 5 → 10 → 20 closed
   SOL overlay trades.
2. **Freeze SOL overlay modifications** — no more session/basis/funding/hour/confidence
   gates on the promoted stack unless live data shows a specific failure mode.
3. **`perp_basis_metrics` retained as infra** — not as the closed SOL long-blocking
   filter. Future: cross-venue basis, short crowding labels, standalone strategies.

---

## Layer-by-Layer Findings

### 1. Strategy engine (`src/strategy/engine.py`)

SELL signals from flat are converted to HOLD when `position_checker` reports no open
position. This prevents orphan SELL closes but also **blocks short entry intent**.

```228:246:src/strategy/engine.py
                if (
                    final_signal.type == SignalType.SELL
                    and self._position_checker is not None
                    and not self._position_checker(symbol, final_signal.trading_mode)
                ):
                    final_signal = Signal(
                        type=SignalType.HOLD,
                        ...
                        reason="No open position — SELL suppressed at engine",
```

**Gap:** No distinction between SELL-as-exit vs SELL-as-short-entry.

### 2. Live futures executor (`src/execution/futures_executor.py`)

Documented LONG-only MVP:

- BUY → open LONG
- SELL → close LONG (`reduceOnly`)
- `_place_sl_tp_orders`: SL below entry, TP above; `position_side` hardcoded LONG
- Software monitor: `mark <= sl` / `mark >= tp`; close always `side="SELL"`
- Live PnL on close: `(exit - entry) × qty` — correct for longs, **wrong for shorts**
- `max_concurrent_longs` exists; no `max_concurrent_shorts`

Liquidation monitoring is SHORT-aware (infers side from `position_amt`) but is
**alert-only** — no order block or force-close.

### 3. Paper executor (`src/execution/paper_executor.py`)

Short infrastructure is the most complete layer:

- `_handle_short_entry` with inverted ATR SL/TP and low-water trailing
- Portfolio writes with `position_side="SHORT"`
- Close via BUY with correct PnL

Gated by `PaperTradingConfig.allow_short_entry` (default **False**). Not wired from
`src/main.py` settings. Default runtime path matches live LONG-only behavior.

`PositionLimitGuard` auto-passes all SELL orders as "closes" — short **entry** SELL
would bypass position-limit checks.

### 4. Backtest engine (`src/backtest/engine.py`)

With `--allow-short`:

| Path | Works? |
|------|--------|
| SELL + flat → open short | Yes |
| BUY + short → close short | Yes |
| SELL + short → close | **No** (no-op) |
| Fixed-% SL/TP | Yes (symmetric) |
| Executor/ATR SL/TP at entry | **Yes** (P0, 2026-06-05) |
| Trailing stop | **Yes** (P0, low-water mark + inverted triggers) |
| Futures margin / PnL | Yes |
| Funding | Always cost; no sign flip for shorts receiving funding |
| Spot short | Synthetic (fee-only); not realistic |

Tests: `tests/test_backtest_executor_exit_model.py` (short TP/SL/trailing),
`tests/test_short_side_parity_audit.py::test_backtest_open_short_sets_inverted_atr_sl_tp`.

### 5. Portfolio (`src/portfolio/models.py`, `manager.py`)

SHORT PnL, liquidation price, and trade side semantics are correct at the model
layer. Live futures close paths do not always use this math.

### 6. Risk / guards (`src/risk/manager.py`, `guards.py`)

Circuit breakers (drawdown, daily loss, consecutive losses) are side-agnostic.
SL cooldown after stop-loss blocks **BUY** re-entry only (long-biased).
`PositionLimitGuard` treats SELL as always allowed.

### 7. Telegram (`src/notifications/telegram.py`)

Trade alerts use BUY/SELL emoji only. Short entry shows `🔴 SELL` — same as closing
a long. No `position_side` / LONG vs SHORT label on alerts or daily summary.

### 8. Reconciliation (`src/execution/reconciliation.py`)

Auto-fix paths assume `closing_side="SELL"` for phantom closes — wrong for SHORT
positions.

---

## Parity Matrix

| Capability | Long (prod) | Short (today) | Blocker severity |
|------------|-------------|---------------|------------------|
| Open from signal | BUY | Blocked (engine + executor) | **P0** |
| Close from signal | SELL | BUY only (paper/backtest) | P1 |
| ATR SL/TP at entry | Yes | Paper yes; live no; backtest **yes** | P1 for live |
| Trailing stop | Yes | Paper yes; live no; backtest **yes** | P1 for live |
| Live PnL on close | Correct | LONG formula | **P0** for live |
| Liquidation handling | Alert | Alert (SHORT-aware) | P2 |
| Position-limit guard on entry | BUY checked | SELL bypassed | P1 |
| Telegram clarity | BUY/SELL | Ambiguous | P2 |
| Backtest ↔ live parity | Good | Partial (backtest fixed) | P1 for live |
| WFO with executor exit model | Good | **Yes** (backtest P0) | Unblocks short WFO research |

---

## Gate Classification (by milestone)

| Milestone | Required | Status |
|-----------|----------|--------|
| **Short WFO / backtest research** | Backtest executor-exit parity | **Done** (2026-06-05) |
| **Paper shadow** | Engine short-entry gate + `allow_short_entry` wiring | **Done** (2026-06-05) |
| **Live / testnet promotion** | Futures short MVP, side-aware PnL, inverted SL/TP, guards, Telegram | Not started |

## Blockers Before Short Research

### Before short WFO (backtest only)

1. ~~Backtest: mirror `_open_long` ATR SL/TP + trailing in `_open_short` /
   `_check_executor_exit`.~~ **Done** (2026-06-05).

### Before paper shadow

2. ~~Strategy engine: allow SELL-from-flat only when `allow_short_entry=True` (default off).~~ **Done**.
3. ~~Wire `strategy.allow_short_entry` from settings into `PaperTradingConfig` + `EngineConfig`.~~ **Done**.

### Before live / testnet (not required for WFO or paper)

4. Live futures: short branch in `on_signal`, inverted SL/TP, BUY close, correct PnL.
5. Wire `allow_short_entry` into live futures config when MVP is ready.

### P1 — before live short promotion

5. `PositionLimitGuard`: do not auto-pass SELL when opening shorts.
6. `max_concurrent_shorts` or unified concurrent position cap by side.
7. Reconciliation: side-aware `closing_side`.
8. SL cooldown: symmetric block after short stop-loss.

### P2 — polish

9. Telegram: include `position_side` on trade alerts.
10. Funding sign in backtest (receive vs pay by side/rate).
11. Spot short: explicitly disallow or document as synthetic-only.

---

## Recommended Engineering Sequence

This audit is **review only**. Implementation brief follows only after P0 fixes land
and parity tests pass.

| Step | Task | Gate |
|------|------|------|
| 1 | Fix backtest short executor-exit parity + tests | **Done** |
| 2 | Wire paper `allow_short_entry`; engine short-entry gate | **Done** |
| 3 | Cheap short crowding probe (funding + premium) | HAS_PULSE / NO_PULSE |
| 4 | Live futures short MVP (testnet only) | Manual testnet checklist |
| 4 | Cheap probe: basis/crowding **short** hypothesis (funding + premium) | HAS_PULSE / NO_PULSE |
| 5 | Surface brief (only if probe pulses) | Human review |
| 6 | Strategy/backtest lane | WFO with parity configs |

Do **not** attach short probes to the promoted SOL long overlay. New standalone surface
only.

---

## Test Coverage Today

| Area | Short tests |
|------|-------------|
| `tests/test_backtest.py` | 1 minimal open/EOD-close |
| `tests/test_backtest_executor_exit_model.py` | None |
| `tests/test_backtest_futures.py` | None |
| `tests/test_paper_executor.py` | Substantial (behind `allow_short_entry=True`) |
| `tests/test_short_side_parity_audit.py` | Documents known gaps (this audit) |

---

## References

- Paper short tests: `tests/test_paper_executor.py` (`test_futures_sell_from_flat_opens_short_position`, etc.)
- Backtest flag: `BacktestConfig.allow_short`, CLI `--allow-short`
- Live LONG-only: `src/execution/futures_executor.py::on_signal` docstring
- Next research path: `docs/reports/autoresearch-next-candidate-path-2026-06-04.md`
- Ledger: `docs/reports/autoresearch-candidate-ledger.md`
