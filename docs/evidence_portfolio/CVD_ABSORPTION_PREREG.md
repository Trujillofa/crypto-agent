# Probe: Binance spot CVD absorption v1

**Status:** SCREEN_FAIL — develop 2026-06-09→2026-07-15 ranked; 0/16 soft-pass; holdout sealed; `promote=no`
**Budget position:** not a third 30-day portfolio probe. Named changed input after
(1) closed OFI decile → forward-return **NO_PULSE** on 2026-05-23 → 2026-06-06 and
(2) MT5 Path 2 `timescale_true_cvd_v1` DISQUALIFY (FP `CopyTicks` quote-only).
Human decision: proceed with plan, 2026-08-20.
**Date pre-registered:** 2026-08-20
**Search id:** `binance_spot_cvd_absorption_v1`
**Machine lock:** `research/rbi_loop/cvd-absorption-v1/lock.json`

This file copies the PREREG-before-data shape of `NFP_PREREG.md`. Fields below were
frozen **before** any REST `aggTrades` download into
`data/microstructure/cvd_absorption_v1/` and before any develop metric was viewed.

---

## Hypothesis

At a **closed** 1-minute bar built from Binance spot `aggTrade` prints, when **price
makes a new N-bar high (low) while cumulative volume delta (CVD) does not** — or the
reverse: CVD makes the N-bar extreme and price does not — a fade or follow entered at
the **next bar’s open** has positive expectancy after 10 bps/side taker plus
tape-measured half-spread.

That is a **level / divergence** claim. It is **not** “high OFI z-score predicts +h
forward VWAP return” (the closed microstructure probe).

CVD is `cumsum(sign_trade_qty)`. Binance `m=true` (buyer is maker) ⇒ aggressor is
seller ⇒ **−qty**. Do not invert. Do not use kline `taker_buy_*` or OHLCV as CVD.

## Market/instrument

Binance **spot** `BTCUSDT` only. ETH/SOL are out of this lock. This tape does **not**
license FP Markets CFD fills.

## Windows

| Role | UTC calendar | Rule |
|------|----------------|------|
| Develop | **2026-06-09 → 2026-07-15** inclusive as dates; interval `[2026-06-09T00:00:00Z, 2026-07-16T00:00:00Z)` | Only window used for ranking |
| Holdout | **2026-07-16 → 2026-08-18** inclusive as dates; interval `[2026-07-16T00:00:00Z, 2026-08-19T00:00:00Z)` | Sealed until a single develop winner is ranked |
| Forbidden for selection | Official OFI 2026-05-23 → 2026-06-06 and overlapping `aggtrades_20260518_20260608.jsonl` | Recycle of a closed lane |

## Entry rule

On closed 1m bar `i` (`i >= lookback_n`), using print high/low and CVD at `i`:

- Prior window is bars `[i-N, i-1]` (completed bars only).
- Price new high: `high[i] > max(high[i-N:i])`. New low: `low[i] < min(low[i-N:i])`.
- CVD new high/low: the same test on `cvd[i]`.
- `price_ext_cvd_not`: price new high and not CVD new high, or price new low and not
  CVD new low.
- `cvd_ext_price_not`: CVD new high and not price new high, or CVD new low and not
  price new low.
- `fade` fades the extreme that fired (price extreme or CVD extreme).
- `follow` follows that extreme.
- Fill at `open[i+1]` (first print in the next 1m bucket). No same-bar fill.

Grid (16, frozen): `lookback_n ∈ {10,20,30,60}` × `{price_ext_cvd_not, cvd_ext_price_not}`
× `{fade, follow}`. `bar_sec=60`, `hold_bars=1`. See lock `grid`.

## Exit rule

Fixed 1-bar hold: exit at `close[i+1]` (last print in the fill bar). No stop, no
target, no discretionary exit. Skip the signal if bar `i+1` is missing.

## Data source

- Tape: Binance REST `GET /api/v3/aggTrades` (`a,p,q,T,m` only).
- Cache: **new** gitignored dir `data/microstructure/cvd_absorption_v1/` only.
  Do not `--refresh-cache` into `data/microstructure/{BTC,ETH,SOL}USDT/` official
  14d files.
- Bars: stream jsonl into closed 1m bars (open/high/low/close/signed_qty). Do not
  materialize tens of millions of trades in RAM.
- Fetch **develop only** first. Holdout download only after develop is ranked.

## Cost assumptions

**10 bps/side** Binance spot taker (no VIP discount) **plus** half-spread measured
from adjacent prints on the same tape (median `|Δprice|/mid/2` in bps). Round-trip
= `2 × (10 + half_spread_bps)`. Not MT5 250 pt. Not futures 4 bps. Spot only — no
funding.

Book for NP / DD: fixed **$10,000** notional per trade, start equity $10,000, no
leverage, no compounding of size.

## Pass threshold (soft gate, develop)

- Trades **n ≥ 40**
- Net profit **NP > 0** after all costs
- Profit factor **≥ 1.10**
- Maximum drawdown **≤ 25%** of start equity

Rank eligible rows (`n≥40` and `NP>0`) by profit factor then expectancy. Unseal
holdout for the **single** soft-gate winner only. If nobody soft-passes, holdout
stays sealed.

## Kill criterion

- Develop soft-fail (no config meets the four gates), or
- Develop pass and holdout `NP≤0` or `PF<1.1` or `n<40`, or
- Median trade-day is a fat-tail-only story after 10 bps, or
- Any parameter change after viewing develop data → verdict **NO_PULSE**.

`promote=false` / `live_go=false`. No live orders. No `python -m src.main`.
No `mode: live`.

## Scope limit

- One read-only research script
- Grid ≤ 16, frozen here
- BTCUSDT only
- No agent, no Docker, no Timescale `:15432`
- No import into mt5-arch `src/mt5_arch`
- No kline `taker_buy_*` as CVD

## Named changed input

- **vs closed OFI:** different claim (absorption vs price, not OFI z → +h return)
  and a new calendar (develop starts 2026-06-09, after official OFI end 2026-06-06).
- **vs MT5 Path 2:** real `aggTrade` prints exist; FP `CopyTicks` last=0 does not.

## Verdict

**YES**, **NO_PULSE**, or **BLOCKED_ON_DATA**

Verdict: **pending** (lock signed before data).

## Lock sign-off

Parameters locked by: Yderf via plan approval (“proceed with plan”), 2026-08-20.

Implementation: `scripts/probe_cvd_absorption_v1.py`. It must refuse `promote=true`,
`live_go=true`, or `taker_fee_bps ≠ 10`. Official 14d paths are not a legal cache-dir.
