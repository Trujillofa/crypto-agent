# Funding Carry Durability Probe v1 — Report

**Verdict:** **BANK**
**Script:** `scripts/probe_funding_carry_durability.py`
**Question:** is the carry's *excess over risk-free* real, forward-durable, and
survivable — i.e. does it justify a paired spot+perp execution build?

- Symbols usable: 3/3; ticks {'BTCUSDT': 2646, 'ETHUSDT': 2646, 'SOLUSDT': 2646}
- Risk-free benchmark: 4.5%; cost drag 2.0%/yr; min excess gate 1.0%

| Symbol | Net/notional % | Cap factor | Net/capital % | Excess vs RF % | Fwd excess % | Worst +move | Carry DD % | G1 | G2 | G3 |
|--------|----------------|------------|---------------|----------------|--------------|-------------|------------|----|----|----|
| BTCUSDT | +5.22 | 1.25 | +4.17 | -0.33 | -3.27 | 23.2% | 0.94 | n | n | Y |
| ETHUSDT | +5.49 | 1.43 | +3.85 | -0.65 | -3.69 | 42.6% | 1.14 | n | n | Y |
| SOLUSDT | +3.25 | 1.42 | +2.29 | -2.21 | -6.39 | 41.9% | 3.91 | n | n | Y |

Periods (net annualized carry on notional):
- BTCUSDT: train +7.81% → forward +1.54% (neg 15.8%, max neg run 18)
- ETHUSDT: train +8.54% → forward +1.16% (neg 15.6%, max neg run 16)
- SOLUSDT: train +7.44% → forward -2.68% (neg 30.0%, max neg run 21)

## Reasons
- excess>risk-free+1.0% on 0/3; forward-excess>0 on 0/3; drawdown ok on 3/3
- excess over risk-free does not survive capital base / forward split
