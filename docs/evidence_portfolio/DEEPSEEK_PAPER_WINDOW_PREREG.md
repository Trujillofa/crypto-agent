# DeepSeek sentiment-macro paper evidence window

**Status:** LOCKED — pre-registered 2026-08-31, before any post-T0 P&L review
**What this is:** a prospective paper evidence protocol for the deployed
sentiment-macro agent on funded DeepSeek.
**What this is not:** a live-promotion gate, a strategy retune, or a score of the
new window. This document does **not** authorize Deploy, provider change, or
live orders.
**Machine lock:** `research/paper_windows/deepseek-sentiment-macro-v1/lock.json`

---

> Fields below were frozen from (1) the exact deployed SHA, (2) the baked
> sentiment-macro config, (3) existing event/trade schemas, and (4) **pre-T0**
> sentiment cadence only. No post-T0 winners, losses, P&L, profit factor, or
> trade outcomes were inspected before this lock.

## Frozen identity

| Field | Value |
|-------|--------|
| Protocol ID | `deepseek-sentiment-macro-paper-window` |
| Version | `1` |
| Deployed SHA | `bc6ea9e1c62b36c82d27b96f2fb2c28d99f2f316` |
| Provider | `deepseek` |
| Model | `deepseek-v4-pro` |
| Window start (T0) | `2026-08-31T15:26:51Z` (container start) |
| Agent ID | `sentiment-macro-bot` |
| Service | `agent_sentiment_macro` |
| Config | `config/settings.sentiment_macro.yaml` |
| Event log | `data/event_log_sentiment-macro-bot.jsonl` |

## Hypothesis under measurement

The deployed paper agent, answering on funded DeepSeek (`deepseek-v4-pro` at
`https://api.deepseek.com/v1`), can be observed for operational health and
paper-trade outcomes **after T0** without mixing in the pre-T0 xAI/DeepSeek
history or the three historical paper positions.

This window does **not** claim an edge. It records evidence. Live promotion is
prohibited without a separate review and authorization.

## Pre-T0 cadence (denominator source)

Measured from `sentiment_score` events with `ts < T0` only:

| Statistic | Value |
|-----------|--------|
| Observations | 1970 |
| First / last | 2026-03-27T12:32:24Z → 2026-08-31T14:28:11Z |
| Median gap | 3605 s (~1h, matches `evaluation_interval_seconds: 3600`) |
| Rate | 12.541 obs/day |
| Historical paper positions | 3, all with `entry_time < T0` — **excluded**, not validation |

## Horizons and minimum denominators

Chosen from that pre-T0 rate, not from post-T0 results:

- **Operational review:** 14 days. Expected ~175 observations at 12.541/day.
  `min_n_observations = 140` (14 × 10 obs/day floor under the measured 12.5).
- **Strategy review:** 30 days. `min_eligible_trades_for_performance_decision = 4`
  (repository sparse WFO minimum). The 3 pre-T0 paper positions do not count.
- **Degradation:** rolling **10** observations, no-answer ≥ **50%** (code defaults
  in `SentimentScorer`). That is ~10 hours at the pre-T0 median gap.

If a horizon arrives with `n` below the frozen minimum, the only allowed
non-failure decision is `CONTINUE_COLLECTING`.

## Inclusion — observations

Include a JSONL event iff:

- `type == sentiment_score`
- `ts >= 2026-08-31T15:26:51Z`
- `agent_id` is `sentiment-macro-bot` or omitted on historical rows

Classification uses `src/sentiment_sources.py`:

- **Answered / live:** `xai_live`, `deepseek_fallback`, `zai_live`
- **No-answer / error:** `xai_error_fallback`, `error_fallback`, `neutral_fallback`
- Unknown `source` counts as **no-answer** (fail-closed)

Every pre-T0 `sentiment_score` is excluded.

## Inclusion — eligible closed paper trades

Include a position iff:

- `agent_id == sentiment-macro-bot`
- `entry_time >= T0`
- `status == closed` and `exit_time` is present
- fill path is `PaperExecutor` (`order_filled` in this agent's event log)

**Exclude** any position entered before T0 even if it closes afterward.
**Exclude** the three historical paper positions. **Exclude** open positions.
**Exclude** Binance/live executor fills.

Exit-reason breakdown uses `order_filled.close_reason` (`STOP_LOSS`,
`TAKE_PROFIT`, time-stop, signal, reconciliation, etc.).

## Metrics to collect (do not score in this PR)

Operational: answered/live %, no-answer/error %, source/provider/model
attribution, rolling-10 degradation state, alert transitions (rising-edge
degradation pages only).

Strategy: eligible trades, realized P&L, profit factor, win rate, drawdown,
symbol breakdown, exit-reason breakdown.

## Safety invariants

The window is valid only while all remain true:

- `mode: paper`
- `PaperExecutor` is the order path (`executor=paper` at startup)
- no Binance order executor is constructed
- degraded sentiment blocks new BUY entries
  (`not sentiment_degraded` in `SentimentMeanReversionStrategy`)

## Interruption / censoring

| Event | Rule |
|-------|------|
| Provider, config, strategy, threshold, or sizing change | **Terminate** the window |
| Restart or outage | **Record** (`system_startup` / gap); do not erase |
| Operational failures | Count with the frozen taxonomy |
| Emergency safety action | **Annotate and terminate** |

## Permitted decisions

- `CONTINUE_COLLECTING` — default; also when a horizon arrives under denominator
- `STOP_OPERATIONAL_FAILURE` — invariant broken, provider/config change, or
  emergency safety action
- `STOP_PERFORMANCE_FAILURE` — only after `n_eligible_trades >= 4` and
  (`realized_pnl <= 0` or `profit_factor < 1.10`)
- `EVIDENCE_COMPLETE` — strategy horizon reached and both denominators met,
  without a stop rule firing

`promote: false`. `live_go: false`. This evidence window **cannot** authorize
live trading.

## Scope limits

- Documentation, this lock, and reporting tests only.
- No strategy, runtime provider, config, sizing, Docker, Deploy, or migration
  edits in the lock PR.
- Do not score the new window here.

## Sign-off

Parameters locked by: protocol PR against `bc6ea9e1c62b36c82d27b96f2fb2c28d99f2f316`,
2026-08-31. Post-T0 performance was not reviewed before freeze.
