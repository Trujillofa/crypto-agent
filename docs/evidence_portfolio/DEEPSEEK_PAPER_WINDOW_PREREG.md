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
| Decision-policy SHA-256 | `lock.json` `decision_policy_sha256` (canonical JSON of `decision_policy` only) |

Taxonomy and every decision-affecting field live inside `decision_policy`. Metadata
(`locked_at`, pre-T0 cadence notes) is outside the digest. Missing or extra
decision fields, or a digest mismatch, are `LockTamper`.

## Hypothesis under measurement

The deployed paper agent, answering on funded DeepSeek (`deepseek-v4-pro`), can be
observed for **operational** health after T0 without mixing pre-T0 xAI/DeepSeek
history or the three historical paper positions.

This window does **not** claim an edge and **cannot** complete a performance
verdict until a separately approved trade denominator exists. Live promotion is
prohibited.

## Pre-T0 cadence (denominator source)

Measured from `sentiment_score` events with `ts < T0` only:

| Statistic | Value |
|-----------|--------|
| Observations | 1970 |
| First / last | 2026-03-27T12:32:24Z → 2026-08-31T14:28:11Z |
| Median gap | 3605 s (~1h, matches `evaluation_interval_seconds: 3600`) |
| Rate | 12.541 obs/day |
| No-answer | 8 / 1970 = 0.406% |
| Historical paper positions | 3, all `entry_time < T0` — **excluded**, not validation |

## Horizons and operational denominator

Chosen from that pre-T0 rate, not from post-T0 results:

- **Operational review:** 14 days. Expected ~175 observations at 12.541/day.
  `min_n_observations = 140` (14 × 10 obs/day floor under the measured 12.5).
- **Strategy review:** 30 days. **No approved performance trade count.**
- **Degradation:** rolling **10** observations, no-answer ≥ **50%**
  (`SentimentScorer` defaults). ~10 hours at the pre-T0 median gap.
- **Window-level operational fail:** `min_answered_pct = 50`. Same floor as
  `degradation_error_pct`. Pre-T0 no-answer rate (0.406%) sat far above it.
  Transient outages **remain in the denominator** (`no_answer_n` counts).

If a horizon arrives under the observation minimum, the only allowed non-failure
decision is `CONTINUE_COLLECTING`.

## Performance denominator — not approved

Three pre-T0 paper positions cannot support a terminal PF/P&L sample (n=3, no
concentration/outlier distribution). The sparse-WFO engineering minimum of 4 is
**not** a prospective paper-performance denominator and is not used here.

Until a **separately approved** denominator is written into `decision_policy`
(new digest, new review):

- `STOP_PERFORMANCE_FAILURE` is unreachable
- `EVIDENCE_COMPLETE` is unreachable
- strategy horizon + operational pass → `INSUFFICIENT_EVIDENCE`
- any later performance-complete state must still require a concentration/outlier
  check (`performance_denominator.concentration_check_required`)

Zero-loss profit factor is **undefined, not valid**. Infinity, NaN, None, or
`gross_loss == 0` cannot pass or fail performance.

## Inclusion — observations

A JSONL event is **in-window** iff:

- `type == sentiment_score`
- `ts >= 2026-08-31T15:26:51Z`
- `agent_id == sentiment-macro-bot` exactly (missing agent → fail closed)

A **valid DeepSeek answer** additionally requires:

- `payload.provider == deepseek`
- `payload.model == deepseek-v4-pro`
- `payload.source` in lock `valid_deepseek_sources` (`deepseek_fallback` only)

Source class uses **lock lists only** (not `src.sentiment_sources`):

- Historical answered labels: `xai_live`, `deepseek_fallback`, `zai_live`
- No-answer: `xai_error_fallback`, `error_fallback`, `neutral_fallback`
- Unknown source → no-answer
- `xai_live` / `zai_live` after T0 are **not** valid DeepSeek answers; they are
  provider mismatches and terminate (`interruptions.provider_model_mismatch`)
- Missing provider or model → identity failure, fail closed

Every pre-T0 `sentiment_score` is excluded.

## Inclusion — eligible closed paper trades

The `positions` table does **not** store executor provenance. An unverified
position is **not** paper.

Include iff all of:

- `agent_id == sentiment-macro-bot` exactly (missing → ineligible)
- `entry_time >= T0`
- `status == closed` and `exit_time` present
- `executor` is not a live/Binance marker
- caller passes `paper_runtime_verified=True` from overlapping
  `system_startup` / startup-diagnostics for this agent covering `entry_time`
  (`executor=paper`, “Paper mode: using internal PaperExecutor”)

If provenance cannot be proven from existing event-log evidence, the row is
ineligible. This PR does not add a DB column or change runtime.

**Exclude** any position entered before T0 even if it closes afterward.
**Exclude** the three historical paper positions. **Exclude** open positions.

## Decision-state semantics

Evaluate in this order. Missing/invalid metrics never yield `EVIDENCE_COMPLETE`.

| Decision | When |
|----------|------|
| `STOP_OPERATIONAL_FAILURE` | Emergency safety, invariant break, identity failure, provider/model mismatch, config/strategy interruption, unproven paper runtime, `n >= 140` with answered% `< 50`, all-no-answer window, active rolling-10 degradation at the operational horizon, or missing operational aggregates at that horizon |
| `CONTINUE_COLLECTING` | Before operational pass |
| `OPERATIONAL_EVIDENCE_COMPLETE` | Operational horizon + `n >= 140` + counts add up + answered% ≥ 50 + not degraded + invariants + no interruption. **Not** a performance verdict |
| `INSUFFICIENT_EVIDENCE` | Strategy horizon after operational pass while `performance_denominator.approved == false`, or required performance aggregates invalid |
| `STOP_PERFORMANCE_FAILURE` | Only if a future approved denominator exists and valid finite PF/P&L fail the frozen bars **and** concentration check passes as an input |
| `EVIDENCE_COMPLETE` | Only if that future approved denominator exists **and** operational pass **and** strategy horizon **and** valid finite aggregates **and** concentration_ok **and** no degradation/interruption. Unreachable in this lock |

`promote: false`. `live_go: false`. This window **cannot** authorize live trading.

## Safety invariants

The window is valid only while all remain true:

- `mode: paper`
- `PaperExecutor` is the order path
- no Binance order executor is constructed
- degraded sentiment blocks new BUY entries

## Interruption / censoring

| Event | Rule |
|-------|------|
| Provider, config, strategy, threshold, or sizing change | **Terminate** |
| Provider/model mismatch on in-window observations | **Terminate** |
| Restart or outage | **Record**; do not erase; outages count in the no-answer denominator |
| Operational failures | Count with the frozen lock taxonomy |
| Emergency safety action | **Annotate and terminate** |

## Scope limits

- Documentation, this lock, and reporting tests only.
- No strategy, runtime provider, config, sizing, Docker, Deploy, or migration
  edits in the lock PR.
- Do not score the new window here.

## Sign-off

Parameters locked by: protocol PR against `bc6ea9e1c62b36c82d27b96f2fb2c28d99f2f316`.
Post-T0 performance was not reviewed before freeze.
