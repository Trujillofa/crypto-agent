# Lane Brief — Daily Trend-Following Breadth Probe v0 (Gate 0)

**Status:** Gate 0 (brief) → Gate 1 (cheap probe) pending
**Author role:** planned by Claude (planner/reviewer); cheap probe to be built by Grok (builder)
**Predecessors / evidence chain:**
- Gate 1 HAS_PULSE: [higher-tf-trend-following-probe-v0.md](higher-tf-trend-following-probe-v0.md) — daily SMA50 long-only beats buy-hold 3/3 symbols (risk-adjusted)
- Gate 2 FAIL (lane closed): [../reports/daily-trend-long-gate2.md](../reports/daily-trend-long-gate2.md) — 0/9 under WFO
- Research rules / banned lanes: [../reports/research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md)

---

## What the prior trend lane proved — and what killed it

The daily long-only SMA trend filter is the **first and only positive surface** after ~1,440
autoresearch runs. Gate 1 showed the *direction* is real across BTC/ETH/SOL. Gate 2 then failed
it under WFO **0/9**, but read the failure modes carefully — they are **structural, not directional**:

| Gate 2 failure mode | Cause | Directional? |
|---|---|---|
| `min_wfo_trades` (7–18 vs 20) | Daily cadence × **only 3 symbols** → too few events per OOS window | No |
| `max_profit_concentration` (92–100%) | A single trend leg dominates each symbol's PnL | No |
| `min_wfo_sharpe` (negative) | One or two big legs + whipsaw between them on a 3-symbol book | Partly |

ETH even posted **+35% / +22% / +19% OOS compound return** across the SMA values — the edge is
*there*, it just can't clear trade-count and concentration gates on a 3-name universe.

## Edge thesis (this lane)

**The daily-trend edge is real but under-sampled.** A 3-symbol book produces too few, too
correlated, too concentrated trend legs to survive WFO. Expanding to a **broader liquid universe**
(top-N USDT pairs) should multiply *independent* trend legs → enough events for 20+ WFO trades and
diversified profit concentration — **without changing the validated long-with-trend rule.**

Hypothesis: an **equal-weight (or vol-targeted) portfolio of the same daily SMA50 long-only filter
applied across N liquid symbols** clears, on a portfolio basis, the trade-count and concentration
gates that the 3-symbol version could not — while keeping the risk-adjusted edge.

## The make-or-break risk (planner flag — the probe MUST measure this)

Crypto is dominated by a **shared market beta**: BTC/ETH/SOL/alts are highly correlated, so "more
symbols" may **not** give independent legs — they may all go long/flat together, leaving
concentration and effective trade count nearly unchanged (just more fees). **The probe's primary
job is to measure realized cross-symbol signal correlation and portfolio profit concentration, NOT
to assume diversification.** If breadth does not materially lower concentration / raise effective
breadth, this lane is NO_PULSE and closes — do **not** reshape into symbol-cherry-picking.

## Why this is not banned re-fishing

| Rule (reset doc §"Allowed next family") | This lane |
|---|---|
| Different primitive | **Portfolio breadth** across a universe — different in kind from a single-symbol filter or MA-length search. The per-symbol rule is *frozen* at the Gate-1 winner (SMA50); no new parameter fishing. |
| Cheap probe first | Read-only portfolio probe before any strategy class / autoresearch. |
| Standalone | Not attached to the SOL overlay. |
| WFO-realistic event count | **This is the entire point** — breadth is the mechanism to reach 20+ WFO trades. |
| Independent directionality | Long-biased like the live agents (honest caveat: this does *not* add a new direction — see below). |
| "New evidence to revisit trend" | The Gate 2 structural-vs-directional split **is** the new evidence; the runbook only bans *reshaping a closed probe*, and this changes the universe primitive, not the rule. |

**Honest caveat:** the reset doc's top-ranked *unexplored* lane is a **news/event calendar filter**
(genuinely new information, independent directionality). This breadth lane is the strongest
*trend-family* continuation, but it is still long-biased beta. If the goal is diversification away
from existing long agents, news/event ranks higher. Flagging for the decision-maker.

## Signal definition (frozen from Gate 1 — no new knobs)

- Daily bars (UTC buckets), per symbol, **resampled from the finest intraday timeframe available
  for that symbol in prod `ohlcv` — prefer 1h, else 4h.** This is a *daily* strategy: a daily SMA50
  only needs daily closes, so 4h→daily is just as valid as 1h→daily. Do **not** gate the universe on
  1h availability (correction to v0: the 1h-only requirement over-specified the data and wrongly
  blocked the lane — see PR #84 review).
- Per-symbol state: `close[t] > SMA50[t]` → long for day *t+1*, else flat. **SMA50 only** (the
  Gate-1 winner). No MA-length search — that fragility is exactly what closed Gate 2's grid.
- Portfolio: equal weight across all currently-long symbols (report a vol-target variant as a
  secondary, not a new optimizable knob).
- One-way fee on each per-symbol state change.

## Universe & data (builder to confirm against prod DB first)

- Target: top **~15–20** USDT pairs by liquidity with ≥ ~2y of history at **any** intraday
  timeframe (1h or 4h) — measured in **daily bars after resampling**, not in 1h rows.
- **Minimum to run: ~8 symbols.** The probe's job is to measure whether breadth cuts concentration;
  ~10 symbols answers that directionally and is far better than the 3-symbol book that failed Gate 2.
  Only **< ~8** eligible symbols is a true **blocked-on-ingestion** stop. (Correction to v0: the
  original 15-symbol floor + 1h gate parked the lane behind an ingestion task that — given BTC/ETH/
  SOL/BNB @1h plus ADA/AVAX/DOGE/DOT/LINK/XRP @4h — is likely unnecessary.)
- **Coverage audit is step 1** and must report, per symbol, the finest available TF and the
  resulting daily-bar depth. Universe is liquidity-defined and **fixed before** the metric run.
- If ~8 symbols *are* reachable, **run the probe** and report all four pulse metrics. If a real
  pulse appears, *that* justifies ingesting more pairs to reach 15–20 — not the reverse.
- Window: 2024-01-01 → 2026-06-01 (same as Gate 1).

## Pulse criteria (encode in the probe)

Vs an equal-weight buy-and-hold of the same universe, the breadth portfolio must:
1. **Effective breadth / concentration:** max single-symbol share of total PnL **< 50%** (the
   3-symbol version was 92–100%) — the core thing that must change.
2. **Trade count:** aggregate state changes imply **≥ 20** trades per prospective WFO OOS window.
3. **Risk-adjusted:** portfolio Sharpe ≥ buy-and-hold Sharpe **and** max DD < buy-and-hold max DD.
4. Report realized mean pairwise correlation of the long/flat signals (diagnostic for why 1 passes/fails).

Verdict:
- **HAS_PULSE** — all four hold → write a bounded standalone breadth-trend strategy + Gate 2 autoresearch under the `daily_trend` gate.
- **WEAK_EDGE / NO_PULSE** — concentration stays high (correlation dominates) or risk-adjusted edge gone → **close the trend family**; pivot to the news/event lane.

## Validation command plan

```bash
# Gate 1 cheap probe (read-only; coverage audit first, then portfolio metrics)
uv run python scripts/probe_daily_trend_breadth.py --json   # builder to create

uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/daily-trend-breadth-probe-v0.md \
  --probe-verdict <HAS_PULSE|WEAK_EDGE|NO_PULSE> --pretty
```

## Guardrails (do not violate)

1. **Per-symbol rule frozen at SMA50.** No MA-length search, no second indicator, no per-symbol tuning.
2. **No symbol cherry-picking** to manufacture a pass — universe is liquidity-defined and fixed before the run.
3. **Measure correlation/concentration; do not assume diversification.**
4. **Long-only**, portfolio equal-weight (vol-target is a reported variant, not a tuned knob).
5. Build daily bars from the **finest available** intraday TF per symbol (1h else 4h) — do not gate
   on 1h. Only **< ~8** eligible symbols is a true blocked-on-ingestion stop; at ~8+ symbols, run.

## Reviewer (Claude) checkpoints

(a) coverage audit honest about universe depth; (b) per-symbol rule frozen at SMA50; (c) correlation
+ concentration actually measured and reported; (d) verdict HAS_PULSE/WEAK_EDGE/NO_PULSE stated
honestly with the four pulse metrics attached.
