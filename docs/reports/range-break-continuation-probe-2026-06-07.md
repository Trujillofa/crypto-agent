# Range-Break Continuation Probe — 2026-06-07 (Skeleton)

**Verdict:** **PENDING** — probe script + spec + test landed; execute on Hetzner prod DB next.
**Script:** `scripts/probe_range_break_continuation.py`
**Spec:** [range-break-continuation-probe-v0.md](../specs/range-break-continuation-probe-v0.md)
**Prerequisite:** [research-reset-2026-06-06.md](./research-reset-2026-06-06.md) — liquidity sweep (mean-reversion after 1h sweeps) CLOSED WEAK_EDGE + explicitly banned.

---

## Planned Run (copy of liquidity pattern)

| Field | Value |
|-------|-------|
| Host | Hetzner `crypto-agent` (prod TimescaleDB) |
| Branch / Worktree | `feat/range-break-continuation-probe` / `crypto-agent-range-break-continuation` (open after merge of liquidity) |
| Symbols | BTCUSDT, ETHUSDT, SOLUSDT |
| Timeframe | 1h |
| Window | 2024-01-01 → 2026-06-01 (or latest available) |
| Lookback | 24 bars |
| Fee drag | 0.08% round-trip |
| Command (example) | `python scripts/probe_range_break_continuation.py` (or the docker equiv used for liquidity) |

---

## Event definition (recap from spec)

- **Long continuation:** high sweeps prior N high **and close > prior high** (breaks out, not rejected) + expansion filters.
- **Short continuation:** low sweeps prior N low **and close < prior low** + expansion.
- Direction: *with* the structural break.
- This is the opposite of the liquidity-sweep "failed breakdown/breakout" (which required close back *inside*).

---

## Expected output sections (after real run)

- Event counts (should be fewer than the "any sweep" liquidity definition, since close-outside is stricter).
- Forward return (after fees) tables for long/short.
- MAE vs baseline.
- Gate summary (same 6 gates).
- Interpretation (does continuation after confirmed 1h breaks show edge + controlled pullbacks?).
- Decision: HAS_PULSE / WEAK_EDGE / NO_PULSE → brief? strategy? close lane?

---

## Commands (repro on Hetzner, adapt from liquidity)

```bash
# After opening the worktree and pulling latest main (post liquidity merge)
ssh crypto-agent "cd /opt/crypto-agent && \
  docker run --rm --network crypto-agent_crypto-net \
  -v /opt/crypto-agent:/app -w /app -e PYTHONPATH=/app \
  --env-file /opt/crypto-agent/.env \
  -e POSTGRES_HOST=timescaledb -e DB_HOST=timescaledb \
  crypto-agent-agent_sentiment_macro:latest \
  python scripts/probe_range_break_continuation.py"
```

Capture output + save as update to this report (add real numbers, verdict, interpretation, decision).

---

## Next after this probe (per reset)

- **Only** if HAS_PULSE: surface brief, cheap strategy skeleton (standalone first), then autoresearch if warranted.
- Otherwise: record the close (WEAK_EDGE or NO_PULSE), fold lesson into reset/ledger, pick *another* primitive outside banned list (no more "fade 1h event", no SOL 1h retuning, no crowding/funding direct, etc.).
- Phase 0 on the two live agents continues regardless.

**Do not** start campaigns or attach anything until this (or successor) probe clears the gate.
