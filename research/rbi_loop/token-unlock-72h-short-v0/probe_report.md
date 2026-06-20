# Token-Unlock 72h Shock Short Probe — Report

**Verdict:** **NO_PULSE**
**Script:** `scripts/probe_token_unlock_shock.py`
**Trade framing:** directional SHORT, enter first bar after unlock, hold 72h.

## STEP 0 — Data feasibility
- Total events: 52
- Usable (fresh Binance data): 49
- Skipped (no data / fetch error): 0
- Skipped (short history): 0
- Blocked: False

## STEP 1 — Pooled short-edge metrics
- Negative 72h (raw): 49.0% of 49
- Mean raw 72h return: +0.98% (median +0.93%)
- Mean short PnL net of 1.0% haircut: -1.98%
- Random-window baseline mean raw: -0.69%
- Excess short vs baseline: -1.68%
- Negative 72h (BTC-relative): 46.9%
- Mean BTC-relative 72h: +1.09%
- H1 raw short pass: **False**
- H2 BTC-relative pass: **False**

## Reasons
- neither gate passed (neg raw 49.0%, mean short net -1.98%, excess -1.68%)
