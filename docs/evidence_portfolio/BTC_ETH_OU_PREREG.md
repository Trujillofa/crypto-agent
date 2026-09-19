# Probe: Binance spot BTC–ETH log-spread OU v1

**Status:** LOCKED — no tape fetch, no develop metric
**Search id:** `binance_spot_btc_eth_logspread_ou_v1`
**Machine lock:** `research/rbi_loop/btc-eth-ou-v1/lock.json`
**Date pre-registered:** 2026-09-19
**promote / live_go:** **no / false**

Named changed input after (1) ETH 4h range-reversion cost rescreen closed in this repo and (2) mt5-arch `xau_xag_logspread_ou_fade_v1` / `eur_gbp_logspread_ou_fade_v1` SCREEN_FAIL. This is **BTC–ETH on Binance spot**, not a recycle.

## Hypothesis

Daily `log(BTCUSDT) − α − β log(ETHUSDT)` with expanding OLS (min 252 completed days, fit on dates **strictly before** the signal close) mean-reverts. Fade `|z|>2` at the **next day’s open**; exit z-cross 0 or 5 days. Both legs. After **10 bps/side** taker on each leg, develop n≥40, PF≥1.2, NP>0, DD≤15%.

## Market

Binance **spot** `BTCUSDT` + `ETHUSDT` daily klines. Not MT5 CFDs. Not futures funding.

## Windows

| Role | UTC | Rule |
|------|-----|------|
| Develop | `[2024-01-01, 2026-01-01)` | Only window for the screen |
| Holdout | `[2026-01-01, 2026-09-01)` | Sealed. Fetch only after develop ranks |
| Forbidden | CVD absorption caches; ETH 4h range cells | Recycle |

## Costs

10 bps/side taker, both legs, no VIP. Start equity $10k, BTC notional $1000, no compound, leverage 1.

## Kill

SCREEN_FAIL if any soft gate fails. Do not retune 252/2/5. Do not add SOL. Do not import into `src/` live agents.

## Not in this PR

No kline download. No simulator metrics. No `docker-compose`. No live.
