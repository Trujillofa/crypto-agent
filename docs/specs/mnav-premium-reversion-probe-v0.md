# Lane Brief + Builder Handoff — mNAV Premium-Reversion Probe v0 (Gate 0 → Gate 1)

**Status:** Gate 0 brief complete → **Gate 1 build handoff to Grok (builder).**
**Author role:** planned/specified by Claude (planner/reviewer). **Implementation by Grok.** Claude
reviews the resulting code + verdict; Claude does not write the probe `.py`.
**Predecessors / context:**
- Program terminal state on the crypto/OHLCV universe: [../reports/research-consolidation-2026-06-19.md](../reports/research-consolidation-2026-06-19.md)
- Carry lane (last in-universe lane) banked: [funding-carry-neutral-probe-v0.md](funding-carry-neutral-probe-v0.md)
- "Different universe" candidate ranking (this lane is #1): see session analysis 2026-06-20
- External idea source: `vibe-investing/mNAV(Market-to-Net-Asset-Value) arbitrage/` (Dennis's dataset + writeup)

---

## Why this lane (first principles)

Every dead lane shared the same five axes: Binance venue, crypto-major asset, OHLCV/funding data,
direction/carry objective, and no counterparty edge against the most-arbitraged book in crypto.
This lane changes **three axes at once** — asset class (equities), data primitive (equity price +
balance-sheet holdings), and objective (relative value, not a price forecast) — so it cannot fail
for the same reason as the program.

**The mechanism is structural, not a statistical hope.** Crypto-treasury companies (hold crypto on
the balance sheet and little else) trade at a **market-cap-to-NAV ratio (mNAV)** that is rarely 1.0.
The premium is *reflexive*: high mNAV → the company issues equity via ATM → buys more crypto →
feeds the narrative → higher mNAV; and it mean-reverts hard when the premium gets extreme or the
crypto rolls over. This is a live 2024–2026 phenomenon (MSTR and a wave of BTC/SOL/ETH treasury
companies). The relative-value thesis: **extreme mNAV vs its own history predicts convergence.**

## The signal (precise definition — implement exactly)

For each treasury equity `i` on each UTC day `t`:

```
crypto_nav_usd(i,t)   = holdings_units(i,t) * crypto_close_usd(crypto_symbol(i), t)
equity_market_cap(i,t)= shares_outstanding(i,t) * equity_close_usd(i,t)
mnav(i,t)             = equity_market_cap(i,t) / crypto_nav_usd(i,t)
```

**Point-in-time discipline (critical):** `holdings_units` and `shares_outstanding` change over time
(ATM issuance is the whole reflexive mechanism). The probe MUST use the **most recent disclosed
value as of day `t`** (step-function forward-fill from filing dates), never today's value applied
to history. Using current shares/holdings on past prices is look-ahead and silently fabricates the
edge — reject any implementation that does this.

`mnav` ignores non-crypto net assets and debt in v0 (documented simplification — most of these
firms are ~pure crypto holders; debt-heavy names like MSTR are a known caveat, flag per-name).

## Data sources (all free, read-only)

| Field | Source | Notes |
|-------|--------|-------|
| Daily equity close | `yfinance` or `stooq` (free) | builder picks; document choice + add to `requirements.txt` only if not present |
| Crypto daily close | Binance public klines | reuse `scripts/download_historical.py::download_klines` (1d) — already in repo |
| `holdings_units` (time series) | filings / disclosures | hand-seeded static CSV, **with filing dates** (see seed schema) |
| `shares_outstanding` (time series) | filings | same static CSV, point-in-time |

**Seed CSV** — `data/treasury_equities/mnav_universe.csv` (builder creates; gitignore allowlist like
`data/token_unlocks/`). One row per (ticker, as_of_date) disclosure event:

```
ticker,crypto_symbol,binance_symbol,as_of_date,holdings_units,shares_outstanding,source
MSTR,BTC,BTCUSDT,2024-01-01,189150,16500000,10-K/8-K filing url
MSTR,BTC,BTCUSDT,2024-07-31,226500,17900000,8-K filing url
DFDV,SOL,SOLUSDT,2025-05-15,317000,14000000,press release url
...
```

> **STEP 0 builder task:** populate this seed from primary filings (8-K/10-Q/press releases) for a
> small universe (target 4–6 names spanning BTC + SOL + ETH treasuries; e.g. MSTR, MetaPlanet,
> DFDV, an ETH-treasury name). Use WebSearch/WebFetch. Each numeric value needs a `source` URL.
> Mark any value you could not verify — do **not** invent holdings/shares. If <4 names can be
> verified with ≥12 months of disclosure history, that is itself the BLOCKED_ON_DATA result.

## STEP 0 — Data feasibility (mandatory gate before any edge claim)

For each seeded ticker: fetch daily equity closes (≥12 months) and join to crypto daily closes and
the forward-filled point-in-time holdings/shares. Count names with a usable, gap-tolerant joined
mNAV series. If `usable_names < MIN_NAMES` (default 4) → **BLOCKED_ON_DATA**, no edge claim.
Report per-name: rows, date span, equity-data source, and any forward-fill gaps > 45 days
(a stale-disclosure flag).

## STEP 1 — Edge test (premium mean-reversion)

Pooled across names (mNAV is unitless and cross-comparable):

- **H1 (mean-reversion):** when a name's mNAV is in its **trailing extreme percentile** (e.g. top
  / bottom decile of its own trailing 180-day window), does mNAV **converge toward its trailing
  median** over the next `H` days (default H ∈ {10, 21}) by more than a matched random-window
  baseline for the same name? Report mean Δmnav (event vs baseline), directional consistency, and a
  per-name breakdown.
- **H2 (tradeable, not single-name):** the convergence holds on **≥ MIN_NAMES_EDGE names** (default
  3), not just MSTR. A premium-reversion that exists only on one ticker is a single security's
  idiosyncratic story, not a universe — that is WEAK_EDGE at best.

Cost/tradeability note for interpretation (not a gate in v0): the realizable trade is
long-crypto / short-equity-premium (or inverse), which needs equity borrow + a stock broker — out
of scope for the probe, flagged for the deploy decision. v0 only answers "is the signal real."

## Verdict semantics (match the other probes)

- **HAS_PULSE** := H1 and H2 both pass. (Authorizes a v1: real cost model + equity-execution
  feasibility audit — NOT deployment.)
- **WEAK_EDGE** := H1 passes pooled but H2 fails (single-name driven).
- **NO_PULSE** := convergence inside baseline/cost noise.
- **BLOCKED_ON_DATA** := STEP 0 fails (universe too thin to test).

## Build contract (file paths, CLI, artifacts, tests)

| Item | Requirement |
|------|-------------|
| Script | `scripts/probe_mnav_premium_reversion.py` |
| Pattern to mirror | `scripts/probe_funding_carry_neutral.py` (frozen dataclasses, `run_probe`/`render_report`/`main`, `--output-dir`, JSON + MD artifacts) |
| Reuse | `download_klines` from `scripts/download_historical.py` for crypto 1d; `get_logger`/`configure_logger` from `src/utils/logger.py` |
| Seed | `data/treasury_equities/mnav_universe.csv` (+ `.gitignore` allowlist entry) |
| Artifacts | `research/rbi_loop/mnav-premium-reversion-v0/{probe_result.json,probe_report.md}` |
| CLI flags | `--symbols`/names, `--start`, `--end`, `--trailing-window-days` (180), `--extreme-pct` (10), `--horizons` (10,21), `--min-names` (4), `--min-names-edge` (3), `--output-dir` |
| Tests | `tests/test_probe_mnav_premium_reversion.py` — unit-test the point-in-time forward-fill (the look-ahead trap), mNAV math on a fixture, percentile/extreme detection, and verdict routing. **No network in tests** (inject fixtures). |
| Constraints | Read-only. No DB writes, no orders, no `--execute`. `ruff check`/`ruff format` clean; `pytest` green; pre-commit hooks pass. |

## Hard "do NOT" list (review will reject on any of these)

- Do **not** apply today's `holdings_units`/`shares_outstanding` to historical prices (look-ahead).
- Do **not** fabricate any holdings/shares value — every number needs a filing `source`.
- Do **not** declare HAS_PULSE off a single name (that's WEAK_EDGE).
- Do **not** add equity execution, broker code, or any deployment path — this is a Gate-1 probe.
- Do **not** trust the vibe-investing repo's numbers; re-derive from primary filings + live prices
  (same lesson as the token-unlock probe: external datasets get re-verified, not consumed).

## Kill criteria

- BLOCKED_ON_DATA → treasury-equity universe with verifiable point-in-time disclosure history is
  too thin; record, move to candidate #2 (Polymarket) — do not hand-fudge the seed.
- NO_PULSE / WEAK_EDGE → premium-reversion isn't a tradeable universe edge; close, document.
- HAS_PULSE → write v1 brief (cost model + equity-execution-rails feasibility); the rails spend is
  a human decision, not an automated one.
