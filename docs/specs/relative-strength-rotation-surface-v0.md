# Relative Strength Rotation Surface v0.1.0

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHOULD",
"SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document
are to be interpreted as described in RFC 2119.

---

## Overview

This specification defines a new candidate-search surface for crypto futures:
**relative strength rotation with controlled pullback entry**.

The first-principles hypothesis is that crypto returns are dominated by a shared
market beta, but capital rotates into stronger assets during risk-on phases. A
tradeable edge MAY exist when a target asset persistently outperforms BTC/ETH,
then pulls back without losing relative strength. This surface trades that
rotation directly instead of sweeping another single-symbol indicator stack.

This is a design specification. It intentionally defines requirements before
implementation so future research does not collapse into another threshold-only
campaign.

---

## Why This Surface

The prior autoresearch waves mostly searched:

- single-symbol technical indicators,
- indicator overlays,
- ATR exits,
- bounded range reversion,
- funding as an overlay or standalone trigger.

Those surfaces produced one deployable SOL 1h candidate and many failures. The
failure pattern was consistent: either too few trades, more trades with worse
risk, or bootstrap collapse. The missing element is a **cross-asset market
hypothesis**.

Relative strength rotation changes the question from:

> "Is this symbol oversold or trending?"

to:

> "Is capital rotating into this symbol versus the crypto market anchor, and can
> we enter after a pullback without losing that leadership?"

That makes the surface different in kind, not just different parameters.

---

## Definitions

| Term | Definition |
|------|------------|
| Target symbol | The instrument being traded, e.g. `ETHUSDT` or `SOLUSDT`. |
| Anchor symbol | Market beta reference used for relative strength, initially `BTCUSDT`; optionally `ETHUSDT` for alt targets. |
| Relative strength (RS) | Target return over lookback minus anchor return over the same lookback. |
| RS persistence | RS remains positive across more than one lookback and does not sharply deteriorate on the current bar. |
| Risk-on anchor regime | Anchor trend and volatility conditions that permit long rotation trades. |
| Controlled pullback | Target price resets toward VWAP/EMA or RSI reset zone without breaking target trend or RS persistence. |
| Rotation entry | A long entry after target leadership persists and a controlled pullback resolves. |
| Rotation failure | Exit or hold condition when target loses leadership versus anchor after entry. |

---

## Hypothesis

**HYP-001**: A long-only relative-strength rotation strategy SHOULD have better
out-of-sample robustness than another standalone target-symbol technical sweep
because it conditions entries on cross-asset capital flow, not only target-local
oscillators.

**HYP-002**: The surface SHOULD be most testable on liquid symbols with enough
history and clean execution:

- `ETHUSDT`,
- `SOLUSDT`,
- `BTCUSDT` only when using ETH or a crypto basket proxy as the anchor,
- optionally `AVAXUSDT` after liquidity and slippage review.

**HYP-003**: Candidate #2 SHOULD start with `ETHUSDT` before adding another SOL
technical-style agent, because SOL already has a live technical candidate and
additional SOL agents require stronger overlap evidence.

---

## Requirements

### Data Requirements

**REQ-001**: The implementation MUST compute target and anchor returns over at
least two configurable lookbacks.

- Rationale: RS based on one lookback is fragile and can select one-bar noise.
- Verification: unit tests cover 4h/24h or equivalent bar-count return
  calculations for target and anchor.

**REQ-002**: The implementation MUST join anchor data to target bars with
no-lookahead semantics.

- Rationale: cross-symbol joins can accidentally leak future anchor closes.
- Verification: a test proves the anchor row timestamp is less than or equal to
  the target decision timestamp and never newer.
- Precedent: reuse the alignment logic in
  `IndicatorReader.fetch_multi_timeframe` / `_join_timeframes`
  (`src/features/reader.py`) as the no-lookahead pattern, but adapt the
  availability rule for cross-symbol data. For the same target and anchor
  timeframe, the join MUST use the anchor bar with `anchor_time <= target_time`;
  same-timestamp closed bars MAY be used because both symbols' 1h/4h candles are
  complete at the decision timestamp. For a different anchor timeframe, the join
  MUST use the latest anchor bar whose close time is less than or equal to the
  target decision time. In all cases, a newer anchor row MUST NOT be joined.

**REQ-002a**: The anchor symbol MUST be a first-class campaign parameter threaded
through `scripts/autoresearch_loop.py` (`--anchor-symbol`), `BacktestConfig`,
`BacktestEngine`, and the reader join — **not** an overlay field alone.

- Rationale: `autoresearch_loop.py` and `BacktestEngine` are single-symbol today
  (`BacktestConfig.symbol` is singular; the loop calls
  `strategy.evaluate(symbol, row)` with one symbol's dict). Without a real
  `--anchor-symbol` param, a campaign launched per Step 5 silently runs
  single-symbol with no anchor and the RS fields are undefined.
- Verification: a campaign run records the resolved `anchor_symbol`, and a test
  asserts the engine fails fast (not silently) if RS fields cannot be computed
  because the anchor was not supplied.

**REQ-003**: The first implementation SHOULD use existing `ohlcv` and
`indicators` data only.

- Rationale: this lets research start without adding a new ingestion dependency.
- Verification: the first campaign can run on production TimescaleDB without
  new tables.

**REQ-004**: The reader/backtest path MUST expose anchor fields under explicit
names such as `anchor_close_price`, `anchor_return_fast`,
`anchor_return_slow`, and `rs_fast`.

- Rationale: strategy code must not infer anchor data from ambiguous indicator
  names.
- Verification: integration tests assert the joined row schema.

**REQ-004a**: Before the first campaign, the implementation MUST verify the anchor
symbol has `ohlcv` + `indicators` coverage over the same window and timeframe as
the target.

- Rationale: the join is only valid where both series exist; gaps produce silent
  NaN/zero RS and false HOLDs rather than an honest error.
- Verification: a coverage check (or the join itself) raises/records when anchor
  rows are missing for target decision bars; partial-coverage campaigns are not
  scored as clean rejects.

**REQ-004b**: The pullback logic MUST confirm the semantics of the existing `vwap`
column at the campaign timeframe before relying on "near VWAP."

- Rationale: VWAP is anchored to a session/window; its meaning on 4h bars depends
  on how `src/features/technical.py` computes it. "Distance from VWAP" is only
  meaningful if the anchor period is known.
- Verification: a note or test documents the VWAP anchoring used; if ambiguous on
  4h, fall back to EMA distance for the pullback gate.

### Signal Requirements

**REQ-005**: The strategy MUST NOT issue a BUY unless the anchor is in a
risk-on or non-panic regime.

- Rationale: relative strength during market-wide liquidation can be false
  strength.
- Verification: tests cover anchor drawdown/panic bars blocking otherwise valid
  target setups.

**REQ-006**: The strategy MUST require positive RS persistence before entry.

- Rationale: the surface is rotation, not generic pullback buying.
- Verification: tests cover HOLD when target pullback is valid but RS is flat or
  negative.

**REQ-007**: The strategy MUST require a controlled pullback before BUY.

- Rationale: buying leadership at maximum extension increases concentration and
  drawdown.
- Verification: tests cover HOLD when RS is strong but target is overextended
  from VWAP/EMA.

**REQ-008**: The strategy SHOULD require pullback resolution, such as reclaiming
VWAP/EMA, RSI reset recovery, or a close above the prior bar high.

- Rationale: this avoids entering during an unresolved falling pullback.
- Verification: tests cover HOLD while pullback is still deteriorating and BUY
  after configured resolution.

**REQ-009**: The strategy MUST include a rotation-failure exit signal or exit
rule.

- Rationale: if target leadership disappears, the original hypothesis is no
  longer valid.
- Verification: backtest tests cover SELL or executor exit when RS falls below
  the configured failure threshold.

**REQ-010**: The strategy MUST support a cooldown measured in bars after its own
entry, rotation-failure exit, or time-stop exit signal.

- Rationale: repeated entries in the same rotation failure caused poor risk in
  earlier densified surfaces.
- Verification: tests cover no immediate re-entry during cooldown.

**REQ-010a**: The first backtest implementation MUST NOT claim stop-loss
cooldown behavior unless execution feedback is added to the strategy row stream.

- Rationale: the current strategy evaluation path sees market rows and emitted
  signals, not executor stop-loss fills. Live stop-loss cooldown belongs to the
  executor/risk configuration (for example `sl_cooldown_minutes`) until the
  backtest engine exposes stop-loss outcomes back to the strategy.
- Verification: the research report distinguishes strategy-local cooldown from
  executor stop-loss cooldown; tests do not assert stop-loss cooldown unless the
  execution feedback path exists.

### Risk And Execution Requirements

**REQ-011**: Initial research MUST be long-only.

- Rationale: short-side futures lifecycle requires a separate execution parity
  review.
- Verification: strategy tests assert no short SELL entry behavior when flat.

**REQ-012**: Backtests MUST use executor-style ATR exits for parity with live
futures candidates.

- Rationale: promotion failures are often caused by backtest/live exit mismatch.
- Verification: config overlays set `backtest_use_executor_exit_model: true`.

**REQ-013**: The first live candidate, if any, MUST use conservative fixed
notional and isolated futures settings consistent with current live risk.

- Rationale: this is a new surface with no forward record.
- Verification: tracked live config uses small fixed notional, isolated margin,
  leverage cap, and one open position.

### Research Process Requirements

**REQ-014**: The first campaign MUST target `ETHUSDT` with `BTCUSDT` as anchor.

- Rationale: ETH is liquid, independent from the live SOL technical candidate,
  and has enough history to test rotation without adding SOL concentration.
- Verification: campaign launcher or overlay records `symbol=ETHUSDT` and
  `anchor_symbol=BTCUSDT`.

**REQ-015**: The first campaign MUST run under the `standard` gate and MUST NOT
use `probe_1h` for promotion.

- Rationale: the original goal is deployable candidates, not weaker near-miss
  labels.
- Verification: campaign command uses `--gate-profile standard`.

**REQ-016**: A candidate MUST have `eligible_for_bootstrap_1000=true` before
bootstrap=1000 validation.

- Rationale: bootstrap=1000 compute should only be spent on candidates that meet
  the stricter pre-filter.
- Verification: `last_result.json` or archive-level scanner confirms the flag or
  recomputed promotion-candidate gate.

**REQ-017**: Any candidate passing bootstrap=1000 MUST undergo entry-overlap
analysis against:

- `agent_sol_1h_trend_pullback_overlay_live`,
- `agent_sentiment_macro`,
- every other promoted live agent.

- Rationale: candidate count matters only when entries add independent evidence.
- Verification: overlap report exists before tracked paper/live config.

---

## Initial Strategy Shape

The first implementation SHOULD be a single strategy named
`relative_strength_rotation`.

Initial signal logic:

1. Load target and anchor rows for the same decision timestamp.
2. Compute:
   - fast target return,
   - slow target return,
   - fast anchor return,
   - slow anchor return,
   - fast RS,
   - slow RS,
   - RS deterioration versus previous bar.
3. Confirm anchor regime:
   - anchor is above a trend filter or not in panic drawdown,
   - anchor volatility is not extreme unless explicitly configured.
4. Confirm target leadership:
   - fast RS > configured minimum,
   - slow RS > configured minimum,
   - RS deterioration is above configured floor.
5. Confirm controlled pullback:
   - target close is near VWAP or EMA,
   - RSI is reset but not panic,
   - ATR regime is not extreme.
6. Confirm resolution:
   - close reclaims VWAP/EMA,
   - or RSI slope turns positive,
   - or close reclaims previous bar high.
7. Emit BUY with confidence proportional to RS strength and pullback quality.
8. Emit SELL or HOLD-exit condition when RS failure occurs while in position.

---

## Candidate Parameters

Initial bounded search MAY vary only these parameters:

| Parameter | Intended Bound |
|-----------|----------------|
| `fast_lookback_bars` | 4-12 on 1h, 3-8 on 4h |
| `slow_lookback_bars` | 18-48 on 1h, 6-18 on 4h |
| `min_fast_rs_pct` | 0.5%-3.0% |
| `min_slow_rs_pct` | 1.0%-8.0% |
| `max_rs_deterioration_pct` | -1.5%-0.0% |
| `max_vwap_distance_pct` | 0.5%-3.0% |
| `rsi_reset_min` | 35-45 |
| `rsi_reset_max` | 55-65 |
| `anchor_max_fast_loss_pct` | 2.0%-5.0% |
| `cooldown_bars` | 3-12 |
| `time_stop_hours` | 12-72 |

The first search MUST NOT vary more than these parameters. Adding more knobs
before a baseline result exists risks recreating the prior threshold-sweep
failure mode.

---

## Implementation Plan

> **Sizing note:** the cross-symbol join (Steps 1 + 3) is the dominant cost of
> this surface — the backtest stack is single-symbol today. Treat the whole
> surface as **medium**, with the join as the critical path; the strategy itself
> (Step 2) is small by comparison.

### Step 1 — Data Join

Add a no-lookahead cross-symbol reader path for backtest and runtime by
**generalizing** `IndicatorReader.fetch_multi_timeframe` / `_join_timeframes`
(`src/features/reader.py`) from a second timeframe to a second symbol. That code
already aligns a second series by timestamp using only completed bars; reuse its
no-lookahead guarantee rather than writing a new join. Same-timeframe
cross-symbol joins MUST allow the same closed timestamp (`anchor_time <=
target_time`), while higher-timeframe anchor joins MUST preserve completed-bar
close-time semantics.

Required output fields:

- `anchor_time`,
- `anchor_data_age_bars`,
- `anchor_close_price`,
- `anchor_return_fast`,
- `anchor_return_slow`,
- `target_return_fast`,
- `target_return_slow`,
- `rs_fast`,
- `rs_slow`,
- `rs_deterioration`.

### Step 2 — Strategy

Add `src/strategy/relative_strength_rotation.py` and export/register it.

The strategy MUST be usable as a single-strategy standalone candidate. It SHOULD
not require the five-vote aggregator stack.

### Step 3 — Anchor Plumbing + Backtest Parity

Thread the anchor symbol end-to-end (per REQ-002a): add `--anchor-symbol` to
`scripts/autoresearch_loop.py`, an `anchor_symbol` field to `BacktestConfig`, and
pass it through `BacktestEngine` into the reader join so each target `row` carries
the `anchor_*` / `rs_*` keys before `strategy.evaluate(symbol, row)` is called.
The engine MUST fail fast if a `relative_strength_rotation` run is launched
without an anchor symbol, rather than running single-symbol silently. Mixed
single-symbol and cross-symbol strategies MAY be rejected initially to keep scope
controlled.

### Step 4 — Autoresearch Family

Add one family:

```text
relative_strength_rotation_standalone
```

The family MUST generate standalone overlays only. It MUST set:

```text
strategy.strategies = [relative_strength_rotation]
strategy.default_trading_mode = futures
trading_execution.exit_rules.backtest_use_executor_exit_model = true
```

### Step 5 — First Campaign

Run one discovery campaign:

```bash
FAMILIES=relative_strength_rotation_standalone \
ANCHOR_SYMBOL=BTCUSDT \
MAX_RUNS=80 \
GATE_PROFILE=standard \
./scripts/run_autoresearch_campaign_remote.sh ETHUSDT 1h w9-eth-1h-relative-strength
```

If 1h overtrades or fails risk gates, run a second campaign only after reviewing
failure mode:

```bash
FAMILIES=relative_strength_rotation_standalone \
ANCHOR_SYMBOL=BTCUSDT \
MAX_RUNS=80 \
GATE_PROFILE=standard \
./scripts/run_autoresearch_campaign_remote.sh ETHUSDT 4h w9-eth-4h-relative-strength
```

Do not run BTC/BNB/SOL variants before ETH has a readable result.

---

## Stop Conditions

Close the surface if the first two ETH campaigns show:

- best WFO trades < 20 (cannot clear the `standard` gate the run is scored
  against — a 15-19 trade "near-miss" is not promotable under REQ-015),
- best P(loss) > 40%,
- best max DD > 15%,
- best Sharpe < 0.3,
- or all positive OOS candidates have concentration > 60%.

Continue only if at least one candidate is close to `promotion_candidate`
(≥ 20 WFO trades and within reach on return / DD / P(loss) / concentration).

---

## Compliance

The surface design is complete when:

1. this specification is committed,
2. implementation tasks are identified,
3. a first campaign can be launched without adding new market-data ingestion,
4. success/failure criteria are explicit,
5. and the surface is clearly different from prior single-symbol threshold
   sweeps.

The surface is promotion-compliant only when a concrete candidate passes:

1. standard gate at bootstrap=100,
2. `promotion_candidate`,
3. bootstrap=1000,
4. entry-overlap analysis,
5. paper/tracked config review,
6. and conservative live deployment review.

---

## Open Questions

1. Should the first target be `ETHUSDT` only, or should `SOLUSDT` be allowed if
   ETH is too sparse?
2. Should anchor regime use only BTC, or a BTC/ETH composite for SOL/AVAX?
3. Should the first implementation support exits on RS failure, or rely on ATR
   and time-stop exits for the initial campaign?

Default answers for v0:

1. start with `ETHUSDT`,
2. use `BTCUSDT` only,
3. include RS failure exit in strategy logic, but keep ATR/time-stop as primary
   hard exits.
