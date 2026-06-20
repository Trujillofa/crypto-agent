# mNAV Premium-Reversion Probe — Report

**Verdict:** **HAS_PULSE**
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

| Ticker | Days | H1 | H=10 edge | H=21 edge | Events (10/21) |
|--------|------|----|-----------|-----------|----------------|
| 3350.T | 465 | Y | +8.0570 | +66.5608 | 275/264 |
| DFDV | 264 | Y | +0.0089 | -0.0053 | 74/63 |
| MSTR | 604 | n | -0.0212 | -0.0359 | 414/403 |
| SBET | 241 | Y | +0.0495 | +0.0738 | 51/40 |

## Reasons
- mNAV mean-reversion clears random baseline on 3 names (3350.T, DFDV, SBET)
