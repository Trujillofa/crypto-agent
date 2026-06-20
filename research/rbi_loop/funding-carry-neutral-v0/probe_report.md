# Delta-Neutral Funding Carry Probe — Report

**Verdict:** **HAS_PULSE**
**Script:** `scripts/probe_funding_carry_neutral.py`
**Framing:** market-neutral yield (long spot + short perp), not direction.

## STEP 0 — Data feasibility
- Symbols: 3; usable: 3
- Ticks per symbol: {'BTCUSDT': 2646, 'ETHUSDT': 2646, 'SOLUSDT': 2646}
- Blocked: False

## STEP 1 — Per-symbol carry

| Symbol | Ticks | Ann carry % | Net ann % | Neg % | Max neg run | Cum net % | H1 | H2 |
|--------|-------|-------------|-----------|-------|-------------|-----------|----|----|
| BTCUSDT | 2646 | +7.22 | +5.22 | 15.8 | 18 | +12.45 | Y | Y |
| ETHUSDT | 2646 | +7.49 | +5.49 | 15.6 | 16 | +13.10 | Y | Y |
| SOLUSDT | 2646 | +5.25 | +3.25 | 30.0 | 21 | +7.70 | Y | Y |

## Reasons
- net annualized carry clears 3.0% on 3 symbols and is harvestable (neg-fraction bounded, cumulative net > 0) on 3
