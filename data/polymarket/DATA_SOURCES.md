# Polymarket Data Sources (Gate-1 probe, read-only)

Verified **2026-06-20** for `scripts/probe_polymarket_calibration.py`. No API key required.

## Gamma API — market metadata & resolution

| Item | Value |
|------|-------|
| Base URL | `https://gamma-api.polymarket.com` |
| List resolved markets | `GET /markets` |
| Single market | `GET /markets/{id}` |

### Query parameters used by the probe

| Param | Purpose |
|-------|---------|
| `closed=true` | Only closed markets |
| `uma_resolution_status=resolved` | UMA-resolved only (excludes `proposed`) |
| `end_date_min` / `end_date_max` | Window filter (default 2024-01-01 → now) |
| `limit` / `offset` | Pagination (100 per page) |
| `order=endDate` | Stable paging |

### Pagination strategy (2026-06-20)

Gamma offset paging hard-caps around **offset 2000** (`ClientResponseError`). The probe:

1. Retries transient HTTP errors with exponential backoff (5 attempts).
2. On soft-cap / error at high offset, switches to **date-windowed** pagination:
   set `end_date_max` to the oldest `endDate`/`closedTime` seen minus 1s, reset offset to 0, repeat
   until `end_date_max <= start`.
3. Records pull completeness in the audit (`pages`, `mode`, `termination`, earliest/latest endDate).
   A pull that errors out surfaces verdict **`PULL_INCOMPLETE`**, not `BLOCKED_ON_DATA`.

### Fields consumed

- `id`, `question`, `slug`, `conditionId`, `clobTokenIds` (YES token = index 0)
- `outcomes`, `outcomePrices` — binary filter; `["0.5","0.5"]` → invalid/refunded
- `umaResolutionStatus` — must be `resolved`
- `closedTime` — resolution timestamp
- `volumeNum` — liquidity gate
- `category` — category-mix / H2 exclusion test

## CLOB API — historical YES prices

| Item | Value |
|------|-------|
| Base URL | `https://clob.polymarket.com` |
| Price history | `GET /prices-history` |

### Query parameters

| Param | Value | Notes |
|-------|-------|-------|
| `market` | YES `clobTokenIds[0]` | Asset / token id |
| `interval` | `max` | Full history |
| `fidelity` | `720` | **12-hour minimum** for resolved markets |

> **Feasibility note:** Sub-12h `fidelity` returns empty `history` for closed markets
> (see [py-clob-client#216](https://github.com/Polymarket/py-clob-client/issues/216)).
> Lead times τ = 24h and 72h are compatible with 12h snapshots: the probe takes the latest
> price point with `t ≤ close_time − τ` (no post-τ leakage).

### Response shape

```json
{"history": [{"t": 1704844803, "p": 0.42}, ...]}
```

`t` = Unix seconds, `p` = YES price in [0, 1].

## Data API (supplementary, not primary)

| Item | Value |
|------|-------|
| Base URL | `https://data-api.polymarket.com` |
| Trades | `GET /trades?market={conditionId}` |

Used during endpoint verification only. The probe uses `prices-history` as the primary
pre-resolution price source per the lane brief.

## Local cache

Pulled metadata is cached to:

```
data/polymarket/resolved_markets_<start>_<end>.jsonl
```

One JSON object per line (`ResolvedMarket` fields). Price history is fetched at probe run
time and is not cached (CLOB responses are large and τ-dependent).

## Exclusions (STEP 0)

| Reason | Rule |
|--------|------|
| `unresolved` | `umaResolutionStatus != resolved` |
| `invalid_refunded` | `outcomePrices == ["0.5","0.5"]` |
| `not_binary` | `len(outcomes) != 2` |
| `low_liquidity` | `volumeNum < --min-liquidity` (default 1000) |
| `disputed_resolution` | Question/description matches disputed/oracle-contested patterns |
| `no_price_at_{τ}h` | No CLOB history point at or before `close − τ` |

## Cost assumption (STEP 1)

Round-trip cost default **2.5%**:

- ~1.0% half-spread (conservative; wide on longshots)
- 0% taker fee on most current CLOB markets
- ~0.5% Polygon gas / settlement allowance
- Slippage buffer

Override with `--round-trip-cost-pct`.
