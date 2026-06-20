# Lane Brief — Delta-Neutral Funding Carry Probe v0 (Gate 0)

**Status:** Gate 1 v0 **HAS_PULSE** → v1 durability refinement **BANK** (2026-06-20). The raw-notional
carry exists but its *excess over risk-free* does not survive a capital base + forward split. No
build. See "## v0 result" and "## v1 durability result" below.
**Author role:** planned by Claude (planner/reviewer); cheap probe to be built and run before any
campaign, config, or live risk.
**Predecessors / context:**
- Program terminal state: [../reports/research-consolidation-2026-06-19.md](../reports/research-consolidation-2026-06-19.md)
- Token-unlock lane rejected at Gate 1 (4th directional null): [token-unlock-72h-short-probe-v0.md](token-unlock-72h-short-probe-v0.md)
- Prior funding work (all **directional**): [funding-crowding-primary-surface-v0.md](funding-crowding-primary-surface-v0.md), `scripts/probe_funding_normalization.py`, `scripts/probe_basis_premium.py`, `scripts/probe_cross_venue_basis.py`
- Funding data in prod: `migrations/008_add_funding_rates_table.sql`

---

## Why this lane (first principles)

Four independent lanes have now nulled — OHLCV-structure mean-reversion, higher-TF trend, the macro
event calendar, and token unlocks. They differ in mechanism but share **one objective: forecast
crypto price direction on liquid assets.** The base rate across four nulls says cheap directional
edges on majors are arbed. The high-value move is to stop varying the lane and change the
**objective function**.

The one primitive that has *never actually been tested* with a non-directional objective is
**funding carry**. Every prior "funding" probe — including the Phase-3 brief — measured forward
*price* returns: funding-as-a-signal-for-direction. `probe_cross_venue_basis.py` says so explicitly
("Measuring forward price returns (not spread self-convergence) is deliberate"). None tested
**carry harvest**: hold a delta-neutral position and collect the funding stream itself.

Delta-neutral carry is structurally different from all four nulls on the two axes that killed them:

1. **It earns yield, not a forecast.** Long spot + short perp (or the inverse) is market-neutral;
   PnL is the funding you collect minus holding costs, *independent of which way price goes*. It
   does not require the direction-prediction capability that has failed four times.
2. **The trade-frequency constraint dissolves.** Carry is a continuous hold accruing funding every
   8h, not a sparse event or a thin signal. The binding constraint that killed both live vehicles
   (forward-validation trade frequency) does not apply to a continuously-held position.

The trigger data is already in prod (`funding_rates`), so this is a cheap, fully-backtestable probe
with **zero look-ahead** (realized historical funding is point-in-time by construction).

## What this lane is NOT (avoid the prior funding traps)

- **Not** "long when funding is extreme and price reverses" — that is the directional crowding bet
  already rejected (Wave 3 BNB funding Sharpe −0.16; Wave 9 SOL funding-norm CLOSED). Do not stack a
  funding vote onto the aggregator.
- **Not** a cross-venue spread arb (already probed as directional).
- **Not** a naked perp short collecting funding — that is directional with funding as a kicker. The
  defining feature here is **delta-neutrality** (both legs).

## Edge thesis (hypotheses to probe)

A delta-neutral spot+perp position on a liquid major collects **net funding that exceeds its
holding costs**, often enough and large enough to be a positive-expectancy yield.

- **H1 (carry exists):** mean funding rate over the history, annualized, exceeds total holding cost
  (entry/exit fees on both legs amortized over hold + ongoing spread/borrow) on ≥ 2 of
  {BTC, ETH, SOL}.
- **H2 (carry is harvestable, not a coin-flip):** the funding stream is **persistently** one-signed
  enough that a simple always-on (or sign-conditioned) carry does not get erased by negative-funding
  windows. Report: fraction of 8h windows with funding < 0, max consecutive negative run, and net
  cumulative carry minus costs.

**HAS_PULSE := H1 and H2.** Exactly one → WEAK_EDGE (e.g. positive mean but frequently negative →
capacity/timing problem, not a clean yield). Neither → NO_PULSE. Insufficient funding coverage →
BLOCKED_ON_DATA.

## STEP 0 — Data feasibility (gate before any edge claim)

Mandatory first step. Audit `funding_rates` coverage for BTC/ETH/SOL over the available history:
rows present, gap fraction, date span. If usable coverage is too thin per symbol (e.g. < ~12 months
of 8h ticks on ≥ 2 symbols) → **BLOCKED_ON_DATA**, no carry claim. Confirm funding sign convention
(Binance: positive funding ⇒ longs pay shorts) before computing who collects.

## Cost realism (this is where carry lives or dies)

Carry is a *small* edge; costs dominate, so the haircut must be honest, not a floor:

- **Two legs, two round trips:** spot taker + perp taker on entry and exit. Amortize over the
  intended hold (carry wants long holds, so per-period fee drag is small — but model it).
- **Ongoing:** spot-perp basis drift / roll, and any borrow if a leg is margined.
- **Funding can flip:** the position *pays* when funding is the wrong sign. H2 exists precisely to
  catch this.
- Use realistic per-symbol Binance fees; do **not** reuse the old engine's broken cost model
  (see [[backtest-cost-tooling-finding]] — it mis-scaled both fees and funding).

If net carry does not clear costs by a clear margin, that is NO_PULSE — do not tune the hold window
to rescue a sub-cost mean (window overfit).

## Execution-feasibility caveat (a PASS is not an agent)

Even HAS_PULSE does **not** yield a deployable agent directly. Delta-neutral carry needs **two legs
held simultaneously** (spot long + USDT-M perp short), rebalanced as price moves — which the current
single-strategy engine and executors are **not** built for (the short-side parity audit already
flagged futures execution as LONG-only MVP, and there is no spot+perp paired-position lifecycle).
So the honest sequence is:

1. Cheap carry-feasibility probe (this brief) → HAS_PULSE / WEAK_EDGE / NO_PULSE.
2. **Only if HAS_PULSE:** a separate execution-feasibility audit (can the system hold and rebalance
   a paired delta-neutral position safely?) — analogous to `short-side-parity-audit-v0.md`.
3. Only then a surface brief. No campaign, config, paper agent, or live risk before that.

## Pre-committed stop rule (the point of this being the *final* bet)

If this probe returns NO_PULSE (or WEAK_EDGE that the execution audit can't make tradeable), that is
the **fifth independent null across two distinct objectives** (direction × 4, yield × 1). At that
point: **accept the terminal state, keep live services as idle monitors, stop opening lanes**, per
the consolidation doc. Do not start a sixth lane on momentum alone.

## How to run (when built — read-only, dry)

```bash
python scripts/probe_funding_carry_neutral.py    # to be built
# artifacts: research/rbi_loop/funding-carry-neutral-v0/{probe_result.json,probe_report.md}
```

Read-only against `funding_rates` (+ `ohlcv`/`perp_basis_metrics` for cost context). No DB writes,
no orders, no `--execute` path. Same Gate-1 verdict semantics as every prior probe.

## Kill criteria

- BLOCKED_ON_DATA → funding coverage too thin to test; record, do not hand-fill.
- NO_PULSE → carry does not clear costs; lane closed, invoke the stop rule above.
- WEAK_EDGE → positive mean but frequently negative / capacity-bound; do not deploy without the
  execution audit proving it's harvestable net of real two-leg costs.

## v0 result (2026-06-20) — HAS_PULSE

Probe ran against the public Binance futures funding history (2646 8h ticks/symbol ≈ 2.4 years,
2024-01 → 2026-06). All three majors clear both gates:

| Symbol | Ann carry % | Net ann % (−2% drag) | Neg-funding % | Max neg run | Cum net % | H1 | H2 |
|--------|-------------|----------------------|---------------|-------------|-----------|----|----|
| BTCUSDT | +7.22 | **+5.22** | 15.8 | 18 ticks (~6d) | +12.45 | ✅ | ✅ |
| ETHUSDT | +7.49 | **+5.49** | 15.6 | 16 ticks (~5d) | +13.10 | ✅ | ✅ |
| SOLUSDT | +5.25 | **+3.25** | 30.0 | 21 ticks (~7d) | +7.70 | ✅ | ✅ |

**Verdict: HAS_PULSE.** Net annualized carry clears the 3% hurdle on all three; negative-funding
fraction is bounded (16% BTC/ETH, 30% SOL) and cumulative net carry is positive over the window.

**What this is — and is NOT.** This is the **known crypto carry premium** (perp longs persistently
pay funding), re-confirmed on independent data — *not* a discovered alpha. The value is that it's
the first lane to survive Gate 1, and it did so by being **market-neutral yield, not a price
forecast** — exactly the objective change the four nulls argued for. Read it as "~5% net APY,
delta-neutral, on deployed capital," which must be judged against the **opportunity cost of that
capital** (stablecoin/T-bill yield is in the same ballpark), not against zero.

**Why the probe alone cannot authorize deployment (it is a screen, not a backtest).** It measured
*only the funding stream*. It did **not** model: (a) mark-to-market of the spot+perp legs and
rebalancing/slippage (the −2% drag is a crude proxy, not a model); (b) liquidation/margin risk on
the short perp during up-spikes; (c) capital efficiency (the spot leg ties up full notional);
(d) the SOL tail (30% negative funding, ~7-day paying runs). A real backtest needs paired spot+perp
prices marked at each funding tick.

**Decision: advance to the execution-feasibility audit, not deployment.** Per this brief's sequence,
HAS_PULSE authorizes **only** a separate audit of whether the system can hold and rebalance a paired
delta-neutral position safely (analogous to `short-side-parity-audit-v0.md`) — the engine currently
has no spot+perp paired-position lifecycle and futures execution is LONG-only MVP. No campaign,
config, paper agent, or live risk until that audit passes. The pre-committed stop rule does **not**
fire — this is the non-null path it was waiting for.

> **Superseded by the v1 durability refinement below (2026-06-20): BANK.** The execution audit was
> **not** run — v1 showed the carry's excess over risk-free does not survive a capital base + forward
> split, so the build is not justified.

## v1 durability result (2026-06-20) — BANK

Before spending engineering on the execution audit, a cheap read-only refinement
(`scripts/probe_funding_carry_durability.py`) re-asked the v0 HAS_PULSE as the *decision* number:
yield on **capital** (not notional), **excess over a 4.5% risk-free benchmark**, a **forward/OOS
split** (train 2024-01→2025-06 vs forward 2025-06→2026-06), and a **margin-stress** check (worst
72h adverse up-move sets the perp buffer, feeding back into the capital base).

| Symbol | Net/notional % | Cap factor | Net/**capital** % | **Excess vs RF** % | **Fwd excess** % | Worst +72h | Train→Fwd carry |
|--------|----------------|------------|-------------------|--------------------|------------------|------------|-----------------|
| BTC | +5.22 | 1.25 | +4.17 | **−0.33** | **−3.27** | 23.2% | +7.81% → +1.54% |
| ETH | +5.49 | 1.43 | +3.85 | **−0.65** | **−3.69** | 42.6% | +8.54% → +1.16% |
| SOL | +3.25 | 1.42 | +2.29 | **−2.21** | **−6.39** | 41.9% | +7.44% → **−2.68%** |

**Verdict: BANK.** Three independent cuts each kill it:
1. **On capital, not notional:** the ~5% becomes ~4% (margin buffer ties up extra capital).
2. **Vs risk-free:** excess over a 4.5% T-bill/stablecoin alternative is **negative on all three**.
   The carry does not beat parking the capital risk-free, before even counting tail risk.
3. **Forward:** carry compressed ~80% from the 2024 bull (train ~8%) into 2025-26 (forward ~1.2%,
   and **negative for SOL**). Forward excess over risk-free is −3% to −6%. It is being arbitraged
   away in real time (consistent with the Ethena-style delta-neutral product boom).

Margin stress confirms the tail: worst 72h up-moves of 23–43% mean the short leg needs a ~40%+ buffer
on ETH/SOL, not 25% — pushing the capital factor up and yield-on-capital down further.

**This is the value of running v1 before building.** v0's "5% market-neutral" was the known carry
premium measured on notional in a bull-funding window. Put on capital, benchmarked against
risk-free, and looked at forward, the tradeable **excess is gone**. A few hours of read-only probe
work avoided a large paired-execution engineering build on a trade that is already compressed.

**Decision: BANK (Option A), now with evidence.** Do not run the execution-feasibility audit. The
program's terminal state holds: even its one non-directional pulse does not clear the opportunity
cost of capital on a forward basis. Keep live services as idle monitors. The carry probes remain
reusable infra — if a future regime (sustained high-funding bull) might re-open the excess, re-run
this v1 to check; do not assume.
