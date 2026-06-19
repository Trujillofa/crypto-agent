# Research Consolidation — 2026-06-19

**Purpose:** Close out the structural-probe + cost-realism investigation and record the
terminal state of the technical-crypto research program. This is the canonical artifact;
the ledger and the 2026-06-06 reset doc point here.

**Related:**
[research-reset-2026-06-06.md](./research-reset-2026-06-06.md),
[closed-family-cost-corrected-rescreen-2026-06-18.md](./closed-family-cost-corrected-rescreen-2026-06-18.md),
[overlay-threshold-sweep-2026-06-18.md](./overlay-threshold-sweep-2026-06-18.md),
[sentiment-vol-filter-sweep-2026-06-19.md](./sentiment-vol-filter-sweep-2026-06-19.md),
[autoresearch-candidate-ledger.md](./autoresearch-candidate-ledger.md).

---

## Executive summary

Three independent investigations now converge on the same result: **there is currently no
viable forward-validation vehicle in the technical-crypto program.**

1. **Tooling was wrong but did not hide an edge** (PRs #94–#98). The backtest engine
   overcharged costs ~3× (fee/slippage) and funding ~8× on 1h futures, and defaulted the
   global trend filter ON. All three were fixed on correctness merit. Re-screening every
   closed mean-reversion/dislocation family at *corrected* costs revived no deployable lane.
   The most cost-sensitive lane (AVAX 4h bollinger) swung −25.3% → +11.1% but still fails on
   profit concentration (70% — one trade drove the PnL). **No closed lane revives into a
   deployable candidate at correct costs.**

2. **The one "deployable" technical agent cannot forward-validate** (PR #99). The SOL 1h
   overlay has 0 fills ever because its aggregator `buy_threshold` (1.07/1.27) exceeds the
   single-vote confidence cap of 1.0, requiring ≥2 confluent BUYs per 1h bar. The threshold
   sweep (corrected costs, production filter) shows this is not rescuable by lowering the
   gate — see table below. Tradeable frequency and a surviving edge are mutually exclusive
   on this lane.

3. **The only agent that has ever traded is idle — root cause found** (live diagnosis,
   2026-06-18/19). `sentiment-macro` (94 closed trades, last 2026-06-01) calls the xAI LLM
   hourly but emits **zero votes**. The recorded sentiment log (5144 obs, 2026-03-26 → 06-19)
   is **99.9% `xai_live`**, scores min 38 / median 72 / max 95, **never below the 35 FUD gate**
   — so the feed is healthy and the sentiment gate has never bound. The blocker is a
   **miscalibrated volatility filter**, not a dead feed or genuine calm: the
   BUY entry requires `atr_pct ≤ 0.005`, a threshold the config comment notes was set from
   BTC/ETH norms (~0.004–0.007), but it runs on **SOLUSDT**, whose `atr_pct` median is
   0.00844 (p10 0.00518 — above the threshold). Only 8.4% of SOL bars clear it. The
   RSI<35 + lower-band dip setup fires ~10% of bars (239/120d, 81/30d), but the low-vol gate
   culls it to **19/120d and 6/30d** — and the cull is structural (oversold dips spike ATR,
   so the strategy's own setups trip its own filter). System-wide, nothing has traded since
   2026-06-01. **Recalibration was then tested (PR #101) and the strategy has no edge at any
   tradeable frequency — the vol filter was protecting it, not hiding it (see second sweep
   below).**

**The binding constraint of the whole program is forward-validation trade frequency, and it
has no solution in the explored space (majors, 1h/4h, OHLCV-derived structure). Both
candidate vehicles have now been swept empirically to a dead verdict at corrected costs.**

---

## Overlay threshold sweep — the decisive evidence

SOLUSDT 1h, corrected costs (#94), global trend filter ON (production mirror), standard
gate. Effective span 2024-01-09 → 2026-02-23 (DB-clamped), OOS = 9 months / 3 WFO windows.

| buy_threshold | trades | trades/mo | wfo_return% | Sharpe | max_DD% | profit_conc% | p_loss% | passes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.50 | 158 | 17.56 | -75.37 | -0.90 | 99.74 | 100.0 | 100.0 | FAIL |
| 0.60 | 121 | 13.44 | -50.73 | -0.49 | 97.51 | 100.0 | 99.6 | FAIL |
| 0.70 | 70 | 7.78 | -26.08 | -0.33 | 93.90 | 100.0 | 98.6 | FAIL |
| 0.80 | 51 | 5.67 | -17.45 | -0.40 | 79.15 | 100.0 | 91.2 | FAIL |
| 0.90 | 41 | 4.56 | +3.42 | 0.36 | 76.67 | 60.4 | 86.8 | FAIL |
| 1.00 | 27 | 3.00 | -21.58 | -0.36 | 79.14 | 100.0 | 94.8 | FAIL |
| 1.07 | 11 | 1.22 | +53.47 | 1.00 | 29.17 | 95.2 | 24.6 | FAIL |
| 1.27 | 6 | 0.67 | +19.42 | 1.05 | 28.43 | 54.0 | 30.4 | FAIL |

**Reading:** The two "good Sharpe" rows are an illusion of small samples — 1.07 earns its
+53% from essentially one trade (concentration 95.2%, only 11 trades, p_loss 24.6%); 1.27 is
6 trades. Every row with forward-validatable frequency (≥2 trades/mo) is *negative* with
catastrophic drawdowns (up to 99.74% at 0.50). There is no edge under the gate at any
threshold, and the production thresholds that look least-bad cannot fill live. **Verdict:
the overlay is not a viable forward-validation vehicle** (pre-registered decision rule,
direction A/C). No filter-OFF follow-up is warranted — the grid is not starved (17.6
trades/mo at 0.50), so the limiter is the frequency/edge tradeoff, not an upstream filter.

---

## Sentiment-macro vol-filter sweep — the second decisive result

SOLUSDT 1h, corrected costs (#94), sentiment held at a constant bullish 72 (synthetic
replay, 100% hit-rate), standard gate. Effective span 2024-01-09 → 2026-02-23. Swept the
`atr_pct_threshold` (the miscalibrated gate from finding 3) plus a filter-off arm (PR #101).

| arm | trades/mo | wfo_return% | Sharpe | passes |
|---|---:|---:|---:|---:|
| 0.005 (current) | 0.11 | -2.80 | -0.60 | FAIL |
| 0.0065 | 0.44 | -5.25 | -0.64 | FAIL |
| 0.0080 | 1.44 | -11.80 | -0.80 | FAIL |
| 0.0085 (≈SOL median) | 1.67 | -16.41 | -1.43 | FAIL |
| 0.0100 | 2.22 | -27.25 | -1.83 | FAIL |
| 0.0125 (≈SOL p90) | 3.44 | -36.91 | -1.94 | FAIL |
| filter_off | 5.11 | -46.24 | -0.97 | FAIL |

**Reading:** perfectly monotonic — every step that loosens the vol gate buys more trades and
*more* loss (−2.80% → −46.24%). The `atr_pct ≤ 0.005` gate was not blocking a good strategy;
it was the only thing keeping the strategy near-flat, by trading almost never. This is the
same root cause as the rest of the program: the strategy is a sentiment-gated mean-reversion
*dip-buyer*, and on these trending assets buying more dips loses more. Not an upstream
starvation case (filter_off reaches 5.11 trades/mo). **Verdict: no setting trades AND keeps
an edge → sentiment-macro is not a viable forward vehicle either.** The recalibration hope
(rec. #1) is resolved negative; the "94 historical trades" were a cost-bug artifact, exactly
like the overlay's superseded DEPLOY_LIVE row.

---

## Recommendation

In priority order:

1. ~~**Recalibrate + re-validate the sentiment-macro volatility filter.**~~ **RESOLVED
   NEGATIVE (PR #101).** The recalibration sweep is done: there is no `atr_pct_threshold`
   (nor filter-off) at which the strategy reaches ≥2 trades/month *and* keeps an edge at
   corrected costs — loosening the gate is monotonically loss-making (see the second sweep
   above). Sentiment-macro is **not** a viable forward vehicle. Do not pursue a percentile
   gate or any further tuning of this strategy. Leave the live service running idle as a
   monitor; do not expect trades.

2. **Accept the terminal state of the technical-crypto probe program — now operative.** No
   closed lane revives at correct costs; *neither* candidate vehicle (overlay #99,
   sentiment-macro #101) survives at any tradeable frequency; the structural-probe surface is
   exhausted. Stop spending cycles re-screening, reshaping, or recalibrating OHLCV-structure
   lanes on majors. Keep Phase 0 weekly as a *monitor* (not an expectation) on whatever is
   live.

3. **Net-new research requires a new data primitive** (out of consolidation scope). The
   reset doc's #1-ranked direction — a news/event-calendar filter requiring *new
   information* — remains unexplored. Cross-venue basis and higher-TF regime are already
   CLOSED (NO_PULSE / no edge). Opening this is a new program, gated by a cheap probe showing
   HAS_PULSE, and is a decision to make deliberately, not by momentum.

**Discipline to carry forward:** the profitable cTrader FX agent derives per-instrument
costs empirically (`derive_sm_pair_costs.py`). The cost-realism saga here is the same lesson
— calibrate costs from data before trusting any backtest verdict.

---

## What stays / what stops

| Item | State |
|------|-------|
| Structural-probe campaigns (OHLCV structure on majors) | **Stopped** — exhausted, no edge at correct costs |
| SOL overlay Phase 0 forward validation | **Not viable** — untradeable gate, no edge beneath it; keep service as monitor only |
| Sentiment-macro | **Not viable** — vol-filter recalibration swept (#101); no setting trades AND holds an edge; keep service as idle monitor |
| Corrected cost/funding defaults (#94) | **Kept** — correctness fix, applies to all future backtests |
| RBI loop tooling + hard rules (cheap-probe HAS_PULSE, `--execute` human gate) | **Kept** — reusable for any future data-first primitive |
| Backtest harnesses (closed-family / overlay / sentiment vol-filter sweeps) | **Kept** — reusable evaluation tooling |
