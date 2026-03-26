# Grok / xAI Implementation Audit

Generated after the sentiment/macro baseline investigation.

## Executive Summary

Grok/xAI is currently used in **two distinct ways** in this repo:

1. **Trading path** — `SentimentMeanReversionStrategy` uses xAI sentiment scores to gate entries/exits.
2. **Operator path** — `OverseerAgent` uses xAI as a Telegram Q&A assistant for status/risk/portfolio questions.

The biggest issue found is **backtest/live mismatch**:
- live sentiment/macro trading uses real xAI calls when `XAI_API_KEY` is present
- backtests typically do **not** have xAI configured, so sentiment falls back to neutral `50.0`
- result: backtests are not evaluating the same decision policy as production

## Where Grok Is Wired Today

### 1) xAI client
- File: `src/overseer/xai.py`
- Single wrapper around `AsyncOpenAI(... base_url="https://api.x.ai/v1")`
- Exposes one method: `chat(messages) -> str`

### 2) Strategy AI
- File: `src/strategy/sentiment_mean_reversion.py`
- `SentimentScorer` queries xAI and converts the JSON response into a 0-100 score
- When no xAI client is available, it returns neutral `50.0`

### 3) Wiring in app startup
- File: `src/main.py`
- Creates `XAIClient` when `settings.ai.enabled` and `XAI_API_KEY` are present
- Injects the scorer into `SentimentMeanReversionStrategy`
- Also uses the same client for the Telegram overseer

### 4) Operator AI
- File: `src/overseer/agent.py`
- `/ask` sends runtime portfolio/risk context to xAI and returns advisory-only answers

## Config Audit by Agent File

| Config | ai.enabled | Strategy uses Grok directly? | Notes |
|---|---:|---:|---|
| `settings.sentiment_macro.yaml` | true | yes | **Primary trading use of Grok today** |
| `settings.yaml` | true | no | Grok only useful here for Overseer/Q&A unless strategy changes |
| `settings.agent2.yaml` | false | no | AI settings present but disabled |
| `settings.btc-4h.yaml` | false | no | AI settings present but disabled |
| `settings.sol_trend_pullback.yaml` | false | no | AI settings present but disabled |
| `settings.sol_trend_pullback_sparse.yaml` | false | no | AI settings present but disabled |
| `settings.sol_breakout_retest.yaml` | false | no | AI settings present but disabled |
| `settings.avax_4h_ma.yaml` | false | no | no active Grok use |
| `settings.btc_1h_mtf.yaml` | false | no | no active Grok use |
| `settings.autoresearch.yaml` | false | no | research bundle only |

## Findings

### A. Backtest parity is broken
`SentimentScorer` returns `50.0` when xAI is missing. That is safe, but it means historical research silently changes strategy behavior.

**Impact:** live and backtest results are not directly comparable.

### B. AI status logging was misleading
Startup diagnostics were reporting `ai=enabled` only when the Telegram overseer existed, not when xAI was actually available for strategy use.

**Impact:** logs could claim AI was disabled while live xAI sentiment was active.

### C. No durable sentiment history
Before this pass, live Grok sentiment decisions were not being persisted in a structured way for replay.

**Impact:** impossible to reproduce or approximate production sentiment gating in later backtests.

### D. One flag controls two different concerns
`ai.enabled` currently gates both:
- trading-time AI dependencies
- operator/overseer AI chat

**Impact:** strategy AI and operator AI are coupled even though they have different failure modes and safety requirements.

### E. xAI output parsing is fragile
`SentimentScorer` relies on prompt discipline and `json.loads(response)`. If the model returns prose or malformed JSON, score falls back to `50.0`.

**Impact:** silent neutralization can hide model quality or contract drift.

### F. No xAI-specific resiliency layer
`XAIClient` has timeout support, but no explicit retry policy, no circuit breaker, no request metrics, and no rate-limit management.

**Impact:** transient provider issues degrade into neutral fallback without much observability.

### G. AI config drift across files
Several agent configs carry provider/model fields even when `ai.enabled=false` and the active strategies do not use AI.

**Impact:** more surface area for confusion and more chances to misread what is really live.

## Improvements Implemented Now

### 1) Live sentiment observation persistence
Fresh sentiment observations are now recorded through the event log with event type:
- `sentiment_score`

Payload includes:
- `symbol`
- `score`
- `source` (`xai_live`, `neutral_fallback`, `xai_error_fallback`)
- `provider`
- `model`
- optional truncated `error`

Neutral fallback is now cached too, so non-AI runs do not spam duplicate low-value observations on every immediate re-evaluation.

This is the first building block for replayable sentiment backtests.

### 2) Startup diagnostics fixed
Startup diagnostics now reflect actual AI availability:
- `disabled`
- `enabled_no_key`
- `enabled`

This removes the previous mismatch where strategy AI could be active while startup logs said AI was disabled.

## Recommended Improvement Plan

### Priority 1 — Backtest parity
1. Add a `SentimentProvider` abstraction with at least:
   - `LiveXAISentimentProvider`
   - `RecordedSentimentProvider`
   - `NeutralSentimentProvider`
   - optional `SyntheticSentimentProvider`
2. Allow backtests to replay recorded sentiment scores by timestamp/symbol.
3. Fail research loudly when a sentiment-gated strategy is tested without a declared sentiment mode.

### Priority 2 — Decouple AI modes
Split config into separate concerns, for example:
- `ai.strategy.enabled`
- `ai.overseer.enabled`
- `ai.provider`
- `ai.model`

This will make it obvious whether Grok is used for trading, chat, or both.

### Priority 3 — Harden xAI response contracts
1. Prefer strict JSON schema / structured output mode if supported by xAI.
2. Add explicit validation for required keys.
3. Emit metrics for parse failures and fallback rate.

### Priority 4 — Observability
1. Prometheus counters:
   - `xai_requests_total`
   - `xai_failures_total`
   - `xai_parse_failures_total`
   - `sentiment_fallback_total`
2. Log sentiment score summaries per symbol over time.
3. Build a small report script to compare live sentiment distribution vs synthetic/replayed distributions.

### Priority 5 — Expand Grok beyond one strategy carefully
Potential high-value uses across agents:
- **TrendPullback / BreakoutRetest**: regime veto or confidence adjustment, not direct signal generation
- **MTF agents**: macro regime confirmation, news-risk suppression around events
- **Simple MA agents**: probably not worth Grok in current form unless used only as a regime blocker

Important rule: Grok should be used as a **risk/context layer**, not a free-form trade generator.

## Suggested Next Coding Steps

1. Build `RecordedSentimentProvider` backed by the event log or a dedicated DB table.
2. Add `--sentiment-mode {live,recorded,neutral,synthetic}` to research/backtest entrypoints.
3. Add metrics and retry/backoff in `XAIClient`.
4. Clean config drift in non-AI agents so disabled AI is visibly disabled and unambiguous.

## Bottom Line

Today, Grok is **valuable** in the repo, but only one production trading strategy truly depends on it. The main weakness is not model quality — it is **architecture around reproducibility and observability**.

Fix that, and Grok becomes an auditable context signal instead of a mysterious production-only edge.
