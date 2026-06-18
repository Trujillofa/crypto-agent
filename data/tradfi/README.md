# TradFi Risk-Complex Proxies (frozen ex-ante set)

Static Yahoo Finance pulls for the cross-asset / TradFi risk-regime Gate 1 probe
(`scripts/probe_cross_asset_risk_regime.py`).

## Frozen proxy set

| Proxy | Yahoo ticker | Role |
|-------|--------------|------|
| `equity_risk` | QQQ | Nasdaq-100 ETF (equity risk-on proxy) |
| `dxy` | DX-Y.NYB | US Dollar Index |
| `us10y` | ^TNX | US 10-year Treasury yield |
| `vix` | ^VIX | CBOE Volatility Index |

No post-hoc proxies. UUP/NQ=F were evaluated during Step 0; QQQ + DX-Y.NYB selected
for cleaner daily coverage over 2024-01-01 → 2026-06-01.

## Granularity

| Series | Window | Rows (approx) | Notes |
|--------|--------|---------------|-------|
| `*_1d.csv` | 2024-01-01 → 2026-06-01 | ~605 each | Full probe window |
| `*_1h.csv` | Yahoo 730d cap | 4k–14k each | Partial overlap; H1 primary uses daily |

Yahoo Finance limits intraday history to ~730 calendar days via `period=730d`.
Start/end date requests beyond that window return empty for 1h. Daily is unaffected.

## Timestamp / session conventions (UTC)

All `close_ts_utc` values are when the bar's close is **known** (point-in-time).

| Proxy | Daily close local time | Timezone |
|-------|------------------------|----------|
| equity_risk (QQQ) | 16:00 | America/New_York |
| dxy (DX-Y.NYB) | 17:00 | America/New_York |
| us10y (^TNX) | 16:00 | America/Chicago |
| vix (^VIX) | 16:00 | America/Chicago |

1h bars: `bar_open_utc` from Yahoo index (exchange-local → UTC); `close_ts_utc = bar_open + 1h`.

## Weekend / holiday gaps

`is_weekend_gap_after=true` when the gap since the previous bar exceeds:
- **1d:** > 3 calendar days (Fri close → Mon/Tue open after holidays)
- **1h:** > 72 hours

Gaps are **not** forward-filled. The probe uses only printed closes; crypto windows
start strictly after `close_ts_utc`.

## Files

Columns: `proxy`, `source_ticker`, `granularity`, `bar_open_utc`, `close_ts_utc`,
`close`, `is_weekend_gap_after`.

- `equity_risk_1d.csv`, `equity_risk_1h.csv`
- `dxy_1d.csv`, `dxy_1h.csv`
- `us10y_1d.csv`, `us10y_1h.csv`
- `vix_1d.csv`, `vix_1h.csv`

## Reproducing

```bash
uv run python scripts/pull_tradfi_data.py
```

Source: [yfinance](https://github.com/ranaroussi/yfinance) (free, no API key).
Licensing: Yahoo Finance terms apply; data is for research/probe use only.
