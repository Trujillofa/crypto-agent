# Zacks MCP listed-equity overlay — v0 (spec)

**Status:** spec — data-proof only (BLOCKED for KEEP)
**Date:** 2026-08-22
**Related:** not CVD absorption · not a perp OHLC retune · not NFP/sentiment

## Why

`~/.cursor/mcp.json` now exposes official Zacks MCP
(`https://mcp.zacksdata.com`). That feed is North American **listed**
fundamentals and **current** ETF holdings. This repo's live vehicles are
crypto perps. The only honest new edge here is an **exogenous overlay**:
listed crypto-equity proxies (MSTR, COIN, MARA) and spot-BTC ETFs (IBIT,
GBTC) as context — a different instrument class than SOL/ETH/BTC candles.

## Question (single, falsifiable, later)

After a licensed **historical** extract exists:

> Do dated changes in IBIT/GBTC holdings or MSTR/COIN statement-quality
> ranks add a gross-positive overlay on an **already** KEEP-eligible perp
> family under standard costs?

Until history exists the answer cannot be measured. Today's MCP print is
not a backtest input.

## 2026-08-22 schema facts

Source: Zacks Investment Research. Numeric values are not pinned here.

- Tools: `get_company_snapshot`, `get_income_statement`, `get_balance_sheet`,
  `get_cash_flow`, `get_etf_holdings`
- Annual statements: 5 years observed (AAPL `periods=40` → 2021–2025)
- Holdings: `symbol` + `top_n`; no as-of history parameter
- No `estimate_observed_ts` — this is not an earnings-surprise lane

## This spec may

- Document the user MCP snippet
- Authorize desk-only **current** snapshots with attribution

## This spec must not

- Start a new perp research family from this overlay
- Re-arm paper/live agents
- Treat current holdings as dated flow
- Commit MCP tokens or live Zacks extracts

## User MCP snippet

```json
{
  "mcpServers": {
    "zacks": {
      "type": "http",
      "url": "https://mcp.zacksdata.com"
    }
  }
}
```

Place in `~/.cursor/mcp.json`. Do not commit project `.mcp.json`.

## Gate

`DATA_PASS` requires dated holdings **or** >=10y PIT statements, a separate
perp cost book, and a written overlay contract that names the host family.
Until then: **BLOCKED**.
