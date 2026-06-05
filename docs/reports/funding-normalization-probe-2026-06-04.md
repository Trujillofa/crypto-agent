# Funding Normalization Probe — 2026-06-04

**Surface:** [`funding-crowding-primary-surface-v0.md`](../specs/funding-crowding-primary-surface-v0.md)
**Script:** `scripts/probe_funding_normalization.py`
**Window:** 2024-01-01 → 2026-06-01, 1h price bars, production TimescaleDB

---

## Default thresholds (probe v0)

| Parameter | Value |
|-----------|-------|
| `entry_threshold` | 0.0005 (0.05%) |
| `exit_threshold` | 0.00015 (0.015%) |
| Horizons | 12h / 24h net of funding drag |
| Concentration cap | 30% |

---

## Production results (default thresholds)

| Symbol | Funding ticks | Long norm events | Mean net 12h | Mean net 24h | Verdict |
|--------|---------------|------------------|--------------|--------------|---------|
| BTCUSDT | 2,646 | **0** | — | — | **NO_PULSE** |
| ETHUSDT | 2,646 | **0** | — | — | **NO_PULSE** |
| SOLUSDT | 2,646 | **6** (0.23%) | −0.43% | −0.20% | **SPARSE** |

**Read:** At 0.05% entry, BTC/ETH never register an extreme-negative → normalization cycle in
this window (max negative funding BTC −0.015%, ETH −0.037%). SOL has 13 ticks below
−0.05% but only 6 completed normalization events; crude net forward edge is negative
and concentration fails the 30% cap.

**Do not implement** `funding_normalization_standalone` from these defaults.

---

## DB extreme check (why BTC/ETH are silent)

| Symbol | Min funding | Max funding | Ticks ≤ −0.05% | Ticks ≥ +0.05% |
|--------|-------------|-------------|----------------|----------------|
| BTCUSDT | −0.015% | +0.088% | 0 | 24 |
| ETHUSDT | −0.037% | +0.102% | 0 | 25 |
| SOLUSDT | −0.303% | +0.119% | 13 | 52 |

Crowding on BTC/ETH in 2024–2026 rarely reaches the default long-side extreme; the
surface may need **symbol-specific thresholds** or a **positive-funding normalization
short** (deferred until short-side parity review).

---

## Next steps (goal still open)

1. **Phase 0** — weekly overlap + PnL once `sol-1h-trend-pullback-overlay-live` has closed trades.
2. **Probe reshape** (if revisiting funding primary):
   - per-symbol `entry_threshold` from historical quantiles,
   - or test positive-extreme → normalization short in probe-only mode (`--include-short-events`),
   - require ≥ 20 events and positive mean net before any implementation.
3. **Merge PR #55** — preserves RS pause + funding spec + probe tooling.
4. **Alternative surface** — if reshaped funding probe still fails, pick another
   first-principles brief (not another 1h technical sweep).

Promotion gates unchanged. Agent-count target remains **5–10**; current deployable
technical count **1**.

---

## Reshape matrix (2026-06-04, production DB)

Command: `python scripts/run_funding_probe_reshape.py` (see
[`candidate-search-options-2026-06-04.md`](./candidate-search-options-2026-06-04.md)).

| Scenario | BTC long | BTC net24h | ETH long | ETH net24h | SOL long | SOL net24h | Verdict (long) |
|----------|----------|------------|----------|------------|----------|------------|----------------|
| fixed_0.05pct | 0 | — | 0 | — | 6 | −0.20% | NO_PULSE / SPARSE |
| fixed_0.015pct | 1 | +3.18% | 12 | +0.19% | 53 | −0.12% | SPARSE / WEAK_EDGE |
| neg_tail_5pct | 17 | +0.45% | 18 | +0.54% | 18 | +0.99% | SPARSE (all) |
| neg_tail_10pct | 22 | +0.26% | 26 | +0.38% | **44** | **+0.06%** | **SOL HAS_PULSE** |
| both_norm_probe | 17+178 short | +0.45% | 18+35 short | +0.54% | 18+20 short | +0.99% | SPARSE (long leg) |

**Read:** Only **SOLUSDT** with **10% negative funding tail** clears the cheap probe
(`44` events, positive mean net on **24h only**; 12h mean still negative). BTC/ETH
remain sparse or concentration-limited at best. **Short-side** events are numerous in
`both_norm_probe` but are **not** cleared for live (parity review required).

**Recommendation:** If pursuing funding-primary further, scope v1 to **SOL-only**
with quantile entry, then re-run WFO/autoresearch — still must pass full
`standard` + bootstrap=1000 + overlap checks. Do not deploy from probe alone.
