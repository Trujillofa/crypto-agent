# mNAV Premium-Reversion Probe — Report

**Verdict:** **WEAK_EDGE**
**Script:** `scripts/probe_mnav_premium_reversion.py`
**Framing:** relative value (equity mNAV vs crypto NAV), not price forecast.

## STEP 0 — Data feasibility
- Names: 4; usable: 4
- 3350.T: 465 rows, span 697d, equity=yfinance, max gap 336d **stale-disclosure**
- DFDV: 264 rows, span 382d, equity=yfinance, max gap 327d **stale-disclosure**
- MSTR: 604 rows, span 878d, equity=yfinance, max gap 568d **stale-disclosure**
- SBET: 241 rows, span 350d, equity=yfinance, max gap 169d **stale-disclosure**
- Blocked: False

## STEP 1 — Per-name mean-reversion

| Ticker | Days | H1 | H=10 edge (p) | H=21 edge (p) | Events (10/21) | Event % |
|--------|------|----|---------------|---------------|----------------|---------|
| 3350.T | 465 | n | +0.5264 (0.205) | +0.1260 (0.423) | 121/118 | 44/45 |
| DFDV | 264 | n | +0.6933 (0.339) | +0.4004 (0.200) | 5/5 | 7/8 |
| MSTR | 604 | n | -1.4397 (0.630) | +1.2310 (0.439) | 181/181 | 44/45 |
| SBET | 241 | Y | +0.9324 (0.000) | +1.6616 (0.000) | 15/14 | 29/35 |

## Reasons
- H1 passes on 1 name(s) (SBET) but needs >= 3 for H2
