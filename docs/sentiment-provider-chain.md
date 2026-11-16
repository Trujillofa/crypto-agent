# Sentiment provider chain

This is the operational reference for the AI provider chain used by the sentiment
agent. It covers `SentimentScorer` → `XAIClient.chat_with_validator`, the only path
that applies the sentiment budget and provider-fallback rules.

General overseer chat uses `XAIClient.chat` directly: it shares the provider clients
and permanent-disable state, but it ignores transient cooldowns, has no 50-second
chain, no Command Code step, and no sentiment-response validation.

## Order and budgets

Requests are attempted in this order. A provider is skipped when its client is not
configured or when it is cooling down.

| # | Provider | Enabled by | Source label | Sentiment timeout |
|---|----------|------------|--------------|-------------------|
| 1 | Primary: `zai`, `deepseek`, or `xai` | `ai.provider` plus its key (`ZAI_API_KEY`, `DEEPSEEK_API_KEY`, or `XAI_API_KEY`) | `zai_live`, `deepseek_fallback`, `xai_live` | 10s |
| 2 | OpenCode Go free model | Code path only; application wiring passes no key (`_opencode_go_key` returns empty) | `opencode_go_free_live` | 10s |
| 3 | NVIDIA NIM | `NVIDIA_API_KEY`; model from `NVIDIA_MODEL` | `nvidia_nim_live` | 45s |
| 4 | Ollama Cloud | `OLLAMA_API_KEY` | `ollama_cloud_live` | 10s |
| 5 | xAI fallback | `XAI_FALLBACK_ENABLED=true` and `XAI_API_KEY`; skipped when xAI is primary | `xai_fallback_live` | 10s |
| 6 | DeepSeek fallback | `DEEPSEEK_API_KEY` | `deepseek_fallback` | 10s |
| 7 | Command Code | `COMMANDCODE_ENABLED=true`, `COMMANDCODE_API_KEY`, `COMMANDCODE_MODEL`; deployed via the opt-in overlay | `commandcode_live` | 10s |

DeepSeek reports `deepseek_fallback` whether it is the primary or a fallback; the
source taxonomy does not distinguish those roles.

The total chain budget is **50s** (`_SENTIMENT_CHAIN_TIMEOUT_SECONDS`). When Command
Code is available, the providers before it share a **40s** prior budget and the final
**10s** is reserved for Command Code. A provider call that runs past its share is
cancelled and recorded as a timeout.

NVIDIA sits ahead of Ollama because the Ollama Cloud free tier has been rate-limiting
almost every hourly poll, while NVIDIA NIM is the fallback that records live scores in
practice. OpenCode Go stays wired off pending provider permission for sentiment
traffic; see [opencode-go-privacy-review.md](./opencode-go-privacy-review.md).

## NVIDIA model fallback

The configured NVIDIA model (`NVIDIA_MODEL`, default `minimaxai/minimax-m3.1-flash`)
is tried first. One live-tested alternate, `nvidia/nemotron-3.5-bolt-e4b`, is tried
only when the first failure identifies the model itself:

- HTTP 400/404 with a model-not-found/unsupported marker, including NVIDIA's
  `Function ... not found for account ...` 404 text.
- In sentiment only, a response that fails the JSON-score contract.

Timeout, 429/quota, auth/permission, transport, and generic 5xx failures do **not**
swap models. All NVIDIA models share the same account quota and free-tier throttle,
so a different model ID cannot restore capacity; it only consumes the remaining
chain budget. A 45s timeout is deliberately longer than the healthy 1–7s response
window to catch the observed 30–58s throttle window, and it leaves no room for a
second model anyway.

Cooldown is deferred while a model-specific failure is still being recovered: if the
alternate succeeds, NVIDIA is not cooled down. If the alternate also fails, the last
error is recorded and the provider enters the normal cooldown. Provider-wide failures
cool down immediately.

The actual answering model is recorded in the observation's `model` field; the source
label stays `nvidia_nim_live` for either model.

## Retries and cooldowns

- Sentiment requests use one attempt per provider (`max_attempts=1`); the chain is the
  retry mechanism.
- A transient failure starts a **120s cooldown** for that provider, spanning two 60s
  sentiment polls. General overseer chat does not apply these transient cooldowns.
- Auth/permission failures (401/402/403) and explicit balance/credit-exhausted
  messages disable the provider until the next UTC day rollover.
- A chain timeout records a transient failure for the provider whose call was in
  flight, so a hung provider does not get retried on the next poll.

## Observation sources

`SENTIMENT_ANSWERED_SOURCES` in `src/sentiment_sources.py` lists the live provider
sources above. Two other sources are not provider answers:

| Source | Meaning | Score |
|--------|---------|-------|
| `error_fallback` | Every configured provider failed | `error_fallback_score` (default 30) |
| `neutral_fallback` | No provider client was configured | 50 |

Both are in `SENTIMENT_NO_ANSWER_SOURCES`. The `provider`/`model` fields on an
`error_fallback` row may name the last failing provider, but the score is synthetic
and must not be treated as that provider's sentiment. Any replay, backtest, or report
that reads the event log must filter these sources with
`is_answered_sentiment_source()`; otherwise synthetic 30/50 rows are indistinguishable
from live sentiment.

## Configuration

Credentials and defaults live in `.env.example`. The chain order, alternate model,
and budgets are code constants in `src/overseer/xai.py`; there are no knobs for them.
Command Code has a separate rollout procedure because its key must stay out of the
shared `.env`: see [commandcode-sentiment.md](./commandcode-sentiment.md).

## Verification

Provider distribution and error rates come from the sentiment event log:

```bash
# Remote (requires ssh crypto-agent)
uv run python scripts/sentiment_report.py --remote

# Local event log
uv run python scripts/sentiment_report.py data/event_log_sentiment-macro-bot.jsonl
```

Look for the `source` counts: live sources confirm which provider answered, while
`error_fallback`/`neutral_fallback` mean the chain produced no live score. NVIDIA
model swaps appear as `nvidia_nim_live` rows with `model=nvidia/nemotron-3.5-bolt-e4b`.

## Non-goals

- Model cycling does not restore shared quota, rate limits, or account balance.
- This chain does not authorize live trading or a production deployment.
- The alternate model list is intentionally small: only IDs verified against the live
  catalog are added.
