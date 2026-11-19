# BTC–ETH log-spread OU v1 — develop screen (CR-3)

**Date:** 2026-11-19
**Search id:** `binance_spot_btc_eth_logspread_ou_v1`
**Prereg:** `docs/evidence_portfolio/BTC_ETH_OU_PREREG.md` (locked 2026-09-19)
**Machine lock:** `research/rbi_loop/btc-eth-ou-v1/lock.json` (unmodified)
**Script:** `research/rbi_loop/btc-eth-ou-v1/screen.py`
**Machine result:** `research/rbi_loop/btc-eth-ou-v1/screen_result.json`
**Tape:** `research/rbi_loop/btc-eth-ou-v1/klines/` (+ `fetch_manifest.json`)

## Verdict: **SCREEN_FAIL**

Two of four soft gates fail (PF 0.59 < 1.2; net P&L −$595.83 ≤ 0). Under the prereg's
kill section the lane closes. promote = no, live_go = false.

## Holdout statement

The holdout `[2026-01-01, 2026-09-01)` was **not fetched, computed, or inspected**. Every
kline request used `endTime = 1767225599999` (2025-12-31T23:59:59.999Z); the script rejects
any row opening at or after 2026-01-01, and the integrity check counts 0 such rows per
symbol. No trade uses a price at or after 2026-01-01.

One disclosure: before the fetch, a reachability check
(`/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1`) returned the single latest daily bar
(open 2026-11-18). That bar is after the holdout window, was not saved, and is not used.

## Parameters (from the lock, unchanged)

| Item | Value |
|---|---|
| Venue / symbols / TF | Binance spot, `BTCUSDT` + `ETHUSDT`, 1d |
| Spread | `log(BTC close) − α − β·log(ETH close)` |
| Fit | expanding OLS, min 252 obs, rows strictly before the signal close |
| Entry | `|z| > 2` at close of day i → enter both legs at open of day i+1 |
| Side | z > 2: short BTC $1000, long ETH β·$1000. z < −2: the opposite |
| Exit | z crosses 0 at a close → exit next open; else open of day i+5 |
| Costs | 10 bps/side taker on every leg fill (4 fills per trade) |
| Book | $10,000 start, $1000 BTC notional, no compounding, leverage 1 |
| Window | develop `[2024-01-01, 2026-01-01)` only |

## Interpretations (the prereg leaves these open; the stricter reading is used)

1. **Warmup history.** The fit needs ≥252 completed days before the first develop signal
   (2024-01-01 close). The fetch starts exactly 252 days earlier, at **2023-04-24**, so the
   2024-01-01 fit uses 252 rows. The expanding window is anchored there; no older history
   is fetched or used. Pre-develop rows are used only as fit input; no signal, entry, or
   exit happens before 2024-01-01.
2. **z definition.** `z_i = (log BTC_i − α̂ − β̂·log ETH_i) / σ̂`, where α̂, β̂ and the residual
   standard error σ̂ (ddof = 2) come from the OLS fit on rows strictly before day i. The fit is
   re-estimated every day; the same causal z series drives entries and exits.
3. **Hedge ratio.** β is the β̂ of the signal day, fixed for the life of the trade.
4. **Max hold.** Taken from the lock's fill rule: exit at the open of day i+5 (i = signal
   day), i.e. 4 days after the i+1 entry. Exit-z is checked at closes i+1 … i+4.
5. **Trades strictly inside develop.** A signal is taken only if its latest possible exit
   (open of day i+5) falls on or before 2025-12-31. No signal was dropped by this rule.
6. **One position at a time.** Signals at a close where a position is still held are
   ignored. The position is flat at the close of its exit day, so a signal at that close is
   eligible. The entry rule is the `|z| > 2` level, not a fresh cross.
7. **Drawdown.** Gate value is mark-to-market at every daily close and every fill (stricter
   than closed-trade), measured against the running equity peak. Closed-trade DD is also shown.
8. **Cache location.** The lock names `data/research/btc_eth_ou_v1` (gitignored). The script
   writes the committed tape to `research/rbi_loop/btc-eth-ou-v1/klines/` and mirrors it to
   the lock's `cache_dir`. No `forbidden_cache_paths` are read.

## Data integrity

Source: `https://api.binance.com/api/v3/klines`, no API key, one request per symbol.

| Check | BTCUSDT | ETHUSDT |
|---|---|---|
| Fetched at (UTC) | 2026-11-19T20:37:24.378Z | 2026-11-19T20:37:24.686Z |
| Rows | 983 | 983 |
| Expected days (2023-04-24 … 2025-12-31) | 983 | 983 |
| First / last date | 2023-04-24 / 2025-12-31 | 2023-04-24 / 2025-12-31 |
| Duplicates | 0 | 0 |
| Missing days (gaps) | 0 | 0 |
| Unexpected timestamps | 0 | 0 |
| Strictly increasing | yes | yes |
| OHLC sane (low ≤ open/close ≤ high, > 0) | yes | yes |
| Rows ≥ 2026-01-01 (holdout) | 0 | 0 |
| Last bar closed at fetch time | yes | yes |
| CSV sha256 | `9d9e84b4…c929ad` | `d83ca58c…40d5b506` |

The two symbols' dates align row for row. Full request URLs and hashes are in
`klines/fetch_manifest.json`.

## Spread / fit summary (develop signal days, n = 731)

| Quantity | First (2024-01-01) | Last (2025-12-31) | Min | Max |
|---|--:|--:|--:|--:|
| β̂ | 1.346 | 1.216 | 1.106 | 1.451 |
| α̂ | 0.205 | 1.476 | −0.524 | 2.262 |
| σ̂ (residual SE) | 0.081 | 0.338 | 0.077 | 0.364 |
| z | 0.53 | 0.54 | −1.91 | 3.99 |

- z mean 1.25, std 1.36, lag-1 autocorrelation 0.986.
- Days with z > 2: 226. Days with z < −2: 0.
- σ̂ grew about 4× over the window.

## Metrics

| Metric | Value |
|---|--:|
| Trades (n) | 49 (all short-spread: short BTC / long ETH) |
| Net P&L | −$595.83 (−5.96% of start equity) |
| P&L before fees | −$369.09 |
| Total fees | $226.74 |
| Profit factor | 0.592 (gross profit $865.88 / gross loss $1,461.71) |
| Win rate | 36.7% (18 W / 31 L) |
| Mean / median net per trade | −$12.16 / −$22.99 |
| Std of net per trade | $63.89 |
| Mean win / mean loss | +$48.10 / −$47.15 |
| Best / worst trade | +$207.89 / −$149.39 |
| Mean hold | 4.0 days |
| Exit reasons | max_hold 48, z_cross_0 1 |
| Max DD (mark-to-market) | 9.68% ($989.29) |
| Max DD (closed-trade) | 9.29% ($948.44) |
| Final equity | $9,404.17 |
| Signals dropped (exit past develop) | 0 |

## Soft gates

| Gate | Threshold | Value | Result |
|---|---|--:|---|
| n trades | ≥ 40 | 49 | PASS |
| Profit factor | ≥ 1.2 | 0.592 | **FAIL** |
| Net P&L | > 0 | −$595.83 | **FAIL** |
| Max DD | ≤ 15% | 9.68% | PASS |

Any failed gate → **SCREEN_FAIL**.

## Trades

All prices are daily opens (Binance spot). P&L in USD, after fees.

| # | Signal close | z | Side | β | Entry open | Exit open | Exit | Hold d | BTC in→out | ETH in→out | BTC leg $ | ETH leg $ | Fees $ | Net $ | Cum $ |
|--:|---|--:|---|--:|---|---|---|--:|---|---|--:|--:|--:|--:|--:|
| 1 | 2024-05-16 | 2.07 | short | 1.265 | 2024-05-17 | 2024-05-21 | z_cross_0 | 4 | 65,235→71,447 | 2,945→3,662 | -95.22 | 308.04 | 4.93 | 207.89 | 207.89 |
| 2 | 2024-08-04 | 2.25 | short | 1.232 | 2024-08-05 | 2024-08-09 | max_hold | 4 | 58,161→61,686 | 2,689→2,682 | -60.61 | -2.94 | 4.52 | -68.07 | 139.82 |
| 3 | 2024-08-09 | 3.20 | short | 1.234 | 2024-08-10 | 2024-08-14 | max_hold | 4 | 60,838→60,587 | 2,599→2,702 | 4.12 | 49.21 | 4.51 | 48.82 | 188.64 |
| 4 | 2024-08-14 | 2.27 | short | 1.237 | 2024-08-15 | 2024-08-19 | max_hold | 4 | 58,683→58,427 | 2,661→2,612 | 4.36 | -22.91 | 4.45 | -22.99 | 165.65 |
| 5 | 2024-08-19 | 2.47 | short | 1.239 | 2024-08-20 | 2024-08-24 | max_hold | 4 | 59,438→64,037 | 2,636→2,762 | -77.37 | 59.30 | 4.62 | -22.69 | 142.96 |
| 6 | 2024-08-24 | 2.54 | short | 1.243 | 2024-08-25 | 2024-08-29 | max_hold | 4 | 64,157→59,035 | 2,768→2,528 | 79.84 | -107.60 | 4.30 | -32.06 | 110.90 |
| 7 | 2024-08-29 | 2.81 | short | 1.246 | 2024-08-30 | 2024-09-03 | max_hold | 4 | 59,359→59,132 | 2,528→2,538 | 3.82 | 5.13 | 4.49 | 4.46 | 115.36 |
| 8 | 2024-09-03 | 2.89 | short | 1.248 | 2024-09-04 | 2024-09-08 | max_hold | 4 | 57,488→54,161 | 2,425→2,274 | 57.87 | -78.04 | 4.36 | -24.53 | 90.83 |
| 9 | 2024-09-08 | 2.97 | short | 1.247 | 2024-09-09 | 2024-09-13 | max_hold | 4 | 54,870→58,132 | 2,297→2,362 | -59.46 | 34.99 | 4.59 | -29.05 | 61.78 |
| 10 | 2024-09-13 | 3.05 | short | 1.247 | 2024-09-14 | 2024-09-18 | max_hold | 4 | 60,498→60,314 | 2,439→2,342 | 3.04 | -49.79 | 4.44 | -51.19 | 10.58 |
| 11 | 2024-09-18 | 3.38 | short | 1.247 | 2024-09-19 | 2024-09-23 | max_hold | 4 | 61,760→63,579 | 2,375→2,581 | -29.45 | 108.29 | 4.63 | 74.21 | 84.79 |
| 12 | 2024-09-23 | 2.25 | short | 1.249 | 2024-09-24 | 2024-09-28 | max_hold | 4 | 63,340→65,770 | 2,647→2,694 | -38.36 | 22.39 | 4.56 | -20.53 | 64.26 |
| 13 | 2024-09-28 | 2.40 | short | 1.253 | 2024-09-29 | 2024-10-03 | max_hold | 4 | 65,858→60,649 | 2,675→2,364 | 79.09 | -145.66 | 4.28 | -70.85 | -6.60 |
| 14 | 2024-10-03 | 3.01 | short | 1.255 | 2024-10-04 | 2024-10-08 | max_hold | 4 | 60,753→62,224 | 2,350→2,423 | -24.22 | 38.93 | 4.57 | 10.14 | 3.54 |
| 15 | 2024-10-08 | 2.68 | short | 1.255 | 2024-10-09 | 2024-10-13 | max_hold | 4 | 62,160→63,206 | 2,441→2,476 | -16.82 | 18.27 | 4.55 | -3.10 | 0.45 |
| 16 | 2024-10-13 | 2.56 | short | 1.256 | 2024-10-14 | 2024-10-18 | max_hold | 4 | 62,870→67,422 | 2,469→2,606 | -72.40 | 69.65 | 4.65 | -7.41 | -6.96 |
| 17 | 2024-10-18 | 2.47 | short | 1.260 | 2024-10-19 | 2024-10-23 | max_hold | 4 | 68,428→67,426 | 2,642→2,623 | 14.64 | -9.23 | 4.50 | 0.92 | -6.04 |
| 18 | 2024-10-23 | 2.64 | short | 1.264 | 2024-10-24 | 2024-10-28 | max_hold | 4 | 66,669→68,022 | 2,525→2,508 | -20.29 | -8.41 | 4.54 | -33.24 | -39.29 |
| 19 | 2024-10-28 | 2.74 | short | 1.266 | 2024-10-29 | 2024-11-02 | max_hold | 4 | 69,962→69,496 | 2,567→2,511 | 6.66 | -27.61 | 4.50 | -25.44 | -64.73 |
| 20 | 2024-11-02 | 2.83 | short | 1.269 | 2024-11-03 | 2024-11-07 | max_hold | 4 | 69,375→75,572 | 2,494→2,722 | -89.33 | 115.86 | 4.74 | 21.78 | -42.94 |
| 21 | 2024-11-07 | 2.01 | short | 1.272 | 2024-11-08 | 2024-11-12 | max_hold | 4 | 75,858→88,648 | 2,895→3,372 | -168.61 | 209.09 | 4.92 | 35.57 | -7.38 |
| 22 | 2024-11-13 | 2.32 | short | 1.281 | 2024-11-14 | 2024-11-18 | max_hold | 4 | 90,375→89,856 | 3,187→3,076 | 5.75 | -44.69 | 4.51 | -43.46 | -50.84 |
| 23 | 2024-11-18 | 2.18 | short | 1.293 | 2024-11-19 | 2024-11-23 | max_hold | 4 | 90,464→98,892 | 3,208→3,328 | -93.16 | 48.34 | 4.73 | -49.55 | -100.39 |
| 24 | 2024-11-23 | 2.11 | short | 1.305 | 2024-11-24 | 2024-11-28 | max_hold | 4 | 97,672→95,863 | 3,394→3,653 | 18.52 | 99.73 | 4.69 | 113.56 | 13.18 |
| 25 | 2024-12-21 | 2.02 | short | 1.355 | 2024-12-22 | 2024-12-26 | max_hold | 4 | 97,292→99,430 | 3,339→3,497 | -21.97 | 64.16 | 4.80 | 37.39 | 50.57 |
| 26 | 2025-01-13 | 2.18 | short | 1.394 | 2025-01-14 | 2025-01-18 | max_hold | 4 | 94,536→104,077 | 3,138→3,474 | -100.93 | 149.34 | 5.04 | 43.38 | 93.95 |
| 27 | 2025-01-18 | 2.29 | short | 1.403 | 2025-01-19 | 2025-01-23 | max_hold | 4 | 104,556→103,707 | 3,308→3,243 | 8.13 | -27.61 | 4.77 | -24.26 | 69.69 |
| 28 | 2025-01-23 | 2.10 | short | 1.413 | 2025-01-24 | 2025-01-28 | max_hold | 4 | 103,910→102,083 | 3,338→3,182 | 17.59 | -65.94 | 4.74 | -53.10 | 16.59 |
| 29 | 2025-01-28 | 2.58 | short | 1.423 | 2025-01-29 | 2025-02-02 | max_hold | 4 | 101,336→100,636 | 3,078→3,118 | 6.91 | 18.41 | 4.86 | 20.46 | 37.05 |
| 30 | 2025-02-02 | 2.89 | short | 1.432 | 2025-02-03 | 2025-02-07 | max_hold | 4 | 97,701→96,554 | 2,870→2,687 | 11.73 | -91.34 | 4.76 | -84.37 | -47.32 |
| 31 | 2025-02-07 | 3.44 | short | 1.437 | 2025-02-08 | 2025-02-12 | max_hold | 4 | 96,507→95,778 | 2,622→2,603 | 7.55 | -10.70 | 4.86 | -8.01 | -55.32 |
| 32 | 2025-02-12 | 3.02 | short | 1.440 | 2025-02-13 | 2025-02-17 | max_hold | 4 | 97,870→96,118 | 2,738→2,661 | 17.90 | -40.43 | 4.82 | -27.35 | -82.67 |
| 33 | 2025-02-17 | 2.77 | short | 1.444 | 2025-02-18 | 2025-02-22 | max_hold | 4 | 95,780→96,182 | 2,744→2,663 | -4.20 | -42.66 | 4.85 | -51.71 | -134.38 |
| 34 | 2025-02-22 | 2.66 | short | 1.448 | 2025-02-23 | 2025-02-27 | max_hold | 4 | 96,551→84,250 | 2,763→2,336 | 127.40 | -223.75 | 4.55 | -100.90 | -235.28 |
| 35 | 2025-02-27 | 3.23 | short | 1.450 | 2025-02-28 | 2025-03-04 | max_hold | 4 | 84,709→86,221 | 2,308→2,149 | -17.86 | -99.69 | 4.82 | -122.37 | -357.65 |
| 36 | 2025-03-04 | 3.66 | short | 1.443 | 2025-03-05 | 2025-03-09 | max_hold | 4 | 87,282→86,222 | 2,172→2,204 | 12.14 | 21.32 | 4.90 | 28.56 | -329.09 |
| 37 | 2025-03-09 | 3.59 | short | 1.434 | 2025-03-10 | 2025-03-14 | max_hold | 4 | 80,734→81,116 | 2,020→1,865 | -4.72 | -110.60 | 4.76 | -120.08 | -449.17 |
| 38 | 2025-03-14 | 3.89 | short | 1.412 | 2025-03-15 | 2025-03-19 | max_hold | 4 | 83,983→82,715 | 1,912→1,932 | 15.10 | 14.70 | 4.82 | 24.98 | -424.19 |
| 39 | 2025-03-19 | 3.37 | short | 1.391 | 2025-03-20 | 2025-03-24 | max_hold | 4 | 86,846→86,082 | 2,056→2,006 | 8.79 | -33.86 | 4.74 | -29.80 | -454.00 |
| 40 | 2025-03-24 | 3.18 | short | 1.374 | 2025-03-25 | 2025-03-29 | max_hold | 4 | 87,498→84,424 | 2,081→1,897 | 35.13 | -121.64 | 4.59 | -91.10 | -545.10 |
| 41 | 2025-03-29 | 3.51 | short | 1.357 | 2025-03-30 | 2025-04-03 | max_hold | 4 | 82,649→82,516 | 1,828→1,795 | 1.60 | -24.41 | 4.69 | -27.50 | -572.60 |
| 42 | 2025-04-03 | 3.38 | short | 1.332 | 2025-04-04 | 2025-04-08 | max_hold | 4 | 83,213→79,163 | 1,817→1,553 | 48.67 | -193.64 | 4.42 | -149.39 | -721.99 |
| 43 | 2025-04-08 | 3.87 | short | 1.299 | 2025-04-09 | 2025-04-13 | max_hold | 4 | 76,322→85,277 | 1,473→1,644 | -117.32 | 150.58 | 4.87 | 28.39 | -693.60 |
| 44 | 2025-04-13 | 3.57 | short | 1.257 | 2025-04-14 | 2025-04-18 | max_hold | 4 | 83,760→84,948 | 1,598→1,584 | -14.18 | -11.12 | 4.52 | -29.81 | -723.41 |
| 45 | 2025-04-18 | 3.40 | short | 1.217 | 2025-04-19 | 2025-04-23 | max_hold | 4 | 84,475→93,443 | 1,589→1,756 | -106.17 | 128.45 | 4.67 | 17.62 | -705.79 |
| 46 | 2025-04-23 | 3.08 | short | 1.180 | 2025-04-24 | 2025-04-28 | max_hold | 4 | 93,691→93,749 | 1,795→1,791 | -0.62 | -2.49 | 4.36 | -7.47 | -713.27 |
| 47 | 2025-04-28 | 2.99 | short | 1.155 | 2025-04-29 | 2025-05-03 | max_hold | 4 | 95,011→96,887 | 1,800→1,842 | -19.74 | 27.15 | 4.36 | 3.05 | -710.22 |
| 48 | 2025-05-03 | 2.83 | short | 1.131 | 2025-05-04 | 2025-05-08 | max_hold | 4 | 95,856→97,030 | 1,834→1,811 | -12.25 | -13.82 | 4.26 | -30.33 | -740.55 |
| 49 | 2025-05-08 | 2.33 | short | 1.107 | 2025-05-09 | 2025-05-13 | max_hold | 4 | 103,262→102,791 | 2,207→2,495 | 4.55 | 144.52 | 4.35 | 144.72 | -595.83 |

## Reproduce

```bash
uv run python research/rbi_loop/btc-eth-ou-v1/screen.py          # from committed tape
uv run python research/rbi_loop/btc-eth-ou-v1/screen.py --fetch  # re-fetch develop scope, then screen
```

The script reads every parameter from `lock.json`, refuses to run if the lock drifts from the
prereg values, and refuses to screen if any integrity check fails. One config was run.

## Disposition (prereg kill section)

SCREEN_FAIL. Per the prereg: no retune of 252/2/5, no SOL, no import into `src/`. The holdout
stays sealed and is not to be fetched for this search id. promote = no, live_go = false.
