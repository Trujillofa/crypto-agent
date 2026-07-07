# Probe #1: NFP good-news-is-good OOS

**Status:** PRE-REGISTERED, not yet run
**Budget position:** one of only **two** allowed edge probes in the 30-day evidence
portfolio (see `README.md` and `FEE_MARGINAL_PREREG.md`). There is no third probe.
**Date pre-registered:** 2026-07-07

Background: the macro/news family is CLOSED (calendar NO_PULSE + surprise WEAK_EDGE;
see `docs/reports/macro-surprise-drift-probe-v0.md`). The NFP good-news-is-good effect
was flagged during that closure as an **OOS-only hypothesis** — the single pre-agreed
exception. This probe tests exactly that hypothesis, once, on out-of-sample data, with
the rules below fixed before any data is viewed.

---

> Parameters below were filled in on 2026-07-07 from the **in-sample report only**
> (`docs/reports/macro-surprise-drift-probe-v0.md`); no OOS data was viewed. They become
> locked when the human signs the line at the bottom of this file.

## Hypothesis

Hot NFP surprises (actual > consensus, `z > 0`) are followed by **positive** BTCUSDT
forward returns, and cold surprises by negative ones — "good news is good", the
**opposite** of the ex-ante risk-off sign frozen in probe v0. In-sample basis (BTCUSDT,
28 events, 2024-01 → 2026-05): hot mean +0.65% vs cold mean −1.09% at +24h, consistent
across BTC/ETH/SOL and across +6h/+24h/+72h horizons.

## Market/instrument

BTCUSDT spot (Binance), 1h candles. BTCUSDT is the **only gated instrument**. ETHUSDT
and SOLUSDT may be computed as consistency checks but carry no weight in the verdict.

## Event window

**OOS = US NFP scheduled releases from 2021-01-08 through 2023-12-08 (36 releases)** —
strictly before the in-sample archive (2024-01-05 → 2026-05-08), so no overlap with the
data the hypothesis was formed on. NFP releases after 2026-05-08 may be appended as
forward confirmation as they occur, but the verdict is decided on the 2021–2023 window.
Releases where point-in-time consensus cannot be recovered are excluded and the
exclusion count reported.

## Entry rule

At the close of the first 1h BTCUSDT candle at or after the release timestamp: if
`z > 0` (hot, same standardized-surprise definition as
`scripts/probe_macro_surprise_drift.py`), enter **long**. If `z ≤ 0`, no position.
One entry per release, fixed notional.

## Exit rule

Fixed time exit: close the position 24 hours after entry (first candle close at or
after entry + 24h). No stop, no target, no discretionary exit.

## Data source

- OHLCV: Binance via `scripts/download_historical.py` (BTCUSDT 1h, 2021-01 → 2024-01).
- Surprises: BLS actuals + Investing.com Wayback-archived consensus, same method and
  same point-in-time caveat as `data/macro_events/us_macro_surprises.csv`. Consensus is
  collected for the OOS window **before** any return is computed.

## Cost assumptions

0.04%/side taker fee + 0.02%/side slippage = **0.12% round trip**, matching the
post-#94 realistic backtest defaults. Spot only — no funding.

## Pass threshold

- Net expectancy > 0 after all costs
- Profit factor ≥ 1.10
- Drawdown acceptable under intended risk model
- Result not dependent on one outlier event

## Kill criterion

If expectancy ≤ 0 after costs, or if the result requires parameter changes after viewing
the data, verdict = **NO_PULSE**.

## Scope limit

- One read-only script
- No optimization
- No feature expansion
- No second event thesis
- No lane expansion

## Verdict

**YES** or **NO_PULSE**

Verdict: _[pending]_

## Lock sign-off

Parameters locked by: _[human signature + date required before the probe script runs]_
