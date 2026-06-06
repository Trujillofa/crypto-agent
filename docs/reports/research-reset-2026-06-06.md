# Research Reset — 2026-06-06

**Purpose:** Stop looping on weak hypotheses. Record what failed, what infra exists,
what stays live, what is banned, and what kind of idea is allowed next.

**Related:** [autoresearch-candidate-ledger.md](./autoresearch-candidate-ledger.md),
[autoresearch-next-candidate-path-2026-06-04.md](./autoresearch-next-candidate-path-2026-06-04.md),
[short-crowding-probe-2026-06-06.md](./short-crowding-probe-2026-06-06.md).

---

## Executive summary

After ~1,440+ autoresearch runs and multiple cheap probes, **one deployable technical
agent** remains: `agent_sol_1h_trend_pullback_overlay_live`. Most recent lanes closed
at probe or WFO — not because of missing code, but because **hypotheses do not survive
walk-forward mechanics**.

**Pause new campaigns** for a short period. The only mandatory operational path is
**Phase 0 weekly** forward validation on live agents.

Next research must be a **new first-principles surface** — not another filter on the
SOL overlay, not crowding mean-reversion, not session/premium/funding gates.

**Queued probe (isolated branch):** liquidity sweep / failed breakout structure
(`feat/liquidity-sweep-probe`).

---

## What surfaces failed?

| Lane | Stage closed | Verdict | One-line reason |
|------|--------------|---------|-----------------|
| SOL 1h overlay filters (session router) | WFO A/B | REJECT | Gated ~17% baseline trades; no risk improvement |
| Basis premium long filter on SOL overlay | WFO A/B | REJECT | 1 block / 36 trades; no DD/P(loss) gain |
| Funding normalization (SOL/BNB/ETH) | Probe + autoresearch | CLOSED | NO_PULSE / 0 passes |
| Vol squeeze bounded (BTC/ETH) | Autoresearch | CLOSED | 0/80 passes |
| RS rotation v1 | Probe | PAUSED | Sparse, negative excess |
| Short crowding (premium/funding) | Probe | WEAK_EDGE | Bullish crowding **continues up**; shorts lose |
| BTC/BNB 1h standalone/overlay | WFO sweeps | CLOSED | No edge / silent |
| AVAX/ETH Wave-2 near-misses | bootstrap=1000 | REJECT | Edge collapsed at b=1000 |
| SOL 4h / MTF breakout campaigns | WFO | REJECT | Sparse or over-trades / negative OOS |
| ETH/BTC 4h regime overlays | Phase 7 | CLOSED | Too sparse or negative |

Full campaign log: [autoresearch-candidate-ledger.md](./autoresearch-candidate-ledger.md).

---

## Repeated failure modes

1. **Continuation beats mean-reversion** — Crowded premium, funding, and bullish
   perp regimes coincide with **upward drift**, not fade. Long filters that block
   crowded longs blocked too few trades; shorts into the same regime lost.

2. **Overlay attachment destroys frequency** — Attaching gates to the promoted
   6-strategy SOL stack either blocks too few trades to matter or too many to pass
   WFO trade floors (≥70% retention, ≥20 WFO trades).

3. **MAE-only is not edge** — Lower adverse excursion without positive forward
   return is a risk hint, not a promotable strategy (basis long filter, short crowding).

4. **WFO fragility** — bootstrap=100 near-misses often collapse at bootstrap=1000
   (AVAX/ETH Wave-2). Single-window concentration and P(loss) stay high on 1h stacks.

5. **Sparse event collapse** — Session windows, normalization events, and niche
   filters produce probe pulse that does not transfer to overlay WFO scale.

6. **Same-market correlation** — Portfolio is long-biased; adding long-only agents
   concentrates selloff risk. Short crowding did not provide independent directionality.

---

## Data / infrastructure now available

| Asset | Status | Use |
|-------|--------|-----|
| TimescaleDB OHLCV 1h/4h (BTC/ETH/SOL + others) | Production | Backtest, probes |
| `perp_basis_metrics` (mark/index/premium/basis) | Backfilled ~21k bars/symbol | Probes, future features |
| `funding_rates` | Backfilled | Probes, funding strategies |
| Session liquidity router code | Default-off | Reusable infra only |
| Basis premium filter code | Default-off | Reusable infra only |
| Short backtest executor-exit parity | Done | Honest short WFO if needed |
| Paper `allow_short_entry` + engine gate | Done | Paper shadow path exists |
| Walk-forward A/B runners | Multiple scripts | Formal lane discipline |
| Multi-agent prod compose | 2 live strategy agents | Phase 0 monitoring |

**Parked (useful, not promoted):** short infra, basis data lane, filter modules.

---

## What is still live?

| Service | Config | Role |
|---------|--------|------|
| `agent_sol_1h_trend_pullback_overlay_live` | `settings.sol_1h_trend_pullback_overlay_live.yaml` | **Only deployable technical** — Phase 0 validation |
| `agent_sentiment_macro` | `settings.sentiment_macro.yaml` | Independent sentiment/macro lane |

**Phase 0 question (unchanged):**

> Does the promoted SOL overlay produce acceptable live behavior?

Milestones: **5 → 10 → 20** closed SOL overlay trades before scaling notional or
cloning the stack.

Weekly track: closed trades, realized PnL, entries/month, SL/TP reliability, fill
quality, overlap with sentiment-macro, regime correlation.

---

## Explicitly banned hypotheses (do not re-run without new evidence)

| Banned | Rationale |
|--------|-----------|
| SOL overlay session / hour gates | WFO REJECT (frequency collapse) |
| SOL overlay basis/premium long block | WFO REJECT (1 block, no risk gain) |
| SOL overlay funding / vol squeeze / RS filters | Prior probes/campaigns closed |
| Crowding mean-reversion as **entry** (long or short) | Basis + short crowding probes: continuation |
| Premium/funding as **direct short entry** | Short crowding WEAK_EDGE |
| More SOL 1h aggregator/threshold tuning | Exhausted; live forward data pending |
| Short live / paper shadow / futures MVP | No probe pulse; infra parked |
| Autoresearch campaigns without cheap-probe HAS_PULSE | Gate discipline |
| Lowering WFO gates to hit agent count targets | Count secondary to gates |

---

## Allowed next hypothesis family

Must satisfy **all**:

1. **Different primitive** — not OHLCV-aggregator overlay patch, not session label,
   not funding/premium tail label alone.
2. **Cheap probe first** — read-only script, HAS_PULSE / WEAK_EDGE / NO_PULSE before
   any strategy class or autoresearch.
3. **Standalone surface** — not attached to promoted SOL overlay for first test.
4. **WFO-realistic event count** — target enough events for 20+ WFO trades if promoted.
5. **Independent directionality** — prefer surfaces that do not pile on existing
   long-biased live agents.

**Recommended next probe:** **Liquidity sweep / failed breakout** — price structure
after sweep-and-reject (see [liquidity-sweep-probe-v0.md](../specs/liquidity-sweep-probe-v0.md)).

---

## Current lane map

| Lane | Status |
|------|--------|
| SOL overlay live | **Phase 0** — keep running |
| Sentiment macro | **Live** — independent |
| SOL overlay filters/routers | **Closed** |
| Basis long filter | **Closed** (data infra kept) |
| Short crowding entry | **Closed** |
| Short infra | **Parked** |
| Liquidity sweep / failed breakout | **Probe queued** (`feat/liquidity-sweep-probe`) |
| New autoresearch campaigns | **Paused** until probe pulse |

---

## Operating rules (from 2026-06-06)

1. **No new campaigns** until liquidity-sweep probe (or successor) shows HAS_PULSE.
2. **Phase 0 weekly** — non-negotiable.
3. **Main branch** — production hotfixes + Phase 0 ops only.
4. **Feature worktree** — `crypto-agent-liquidity-sweep` on `feat/liquidity-sweep-probe`.
5. **Merge to main** only after probe report + human review; no live changes from probes.
