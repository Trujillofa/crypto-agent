# Autoresearch Profitability Search - 2026-06-03

## Summary

The Karpathy-style autoresearch loop is now implemented for this repo as a bounded,
config-only research controller. It generated candidate YAML overlays, evaluated
them through the fixed `experiment_autopilot` WFO/bootstrap harness, and recorded
`keep`/`discard` decisions in TSV artifacts.

The search found one candidate that clears the standard WFO/bootstrap gate:

- Symbol/timeframe: `SOLUSDT 1h`
- Tracked paper/probe config:
  `config/settings.sol_1h_trend_pullback_overlay_paper.yaml`
- Tracked risk guardrails:
  `config/risk.sol-1h-trend-pullback-overlay-paper.yaml`
- Validation artifact:
  `/tmp/crypto-agent-autoresearch-tracked-sol-1h-trend-pullback-overlay/archive/experiment-autopilot-20260603-205536-773725-2bdb15-20260603-155544.json`
- Gate profile: `standard`
- Bootstrap samples: `1000`
- Result: `PASS`

The earlier threshold-only search also found one candidate worth a limited probe:

- Symbol/timeframe: `SOLUSDT 1h`
- Tracked paper/probe config:
  `config/settings.sol_1h_probe_paper.yaml`
- Tracked risk guardrails:
  `config/risk.sol-1h-probe-paper.yaml`
- Validation artifact:
  `/tmp/crypto-agent-autoresearch-tracked-sol-1h-probe/archive/experiment-autopilot-20260603-161242-382648-8b5e0e-20260603-111250.json`
- Gate profile: `probe_1h`
- Result: `PASS`

This is not a full production-promotion pass under the standard 1h profile. It
passes all risk and profitability gates, but the standard aggregate WFO trade
gate requires 20 trades and this candidate has 17.

## Best Candidate

### Standard-Gate Candidate

| Metric | Value |
|---|---:|
| Full-period trades | 43 |
| Full-period win rate | 51.16% |
| Full-period return | 12.46% |
| Full-period max drawdown | 7.39% |
| Full-period Sharpe | 0.79 |
| WFO windows | 7 |
| Aggregate WFO trades | 26 |
| Mean OOS Sharpe | 1.72 |
| Compound OOS return | 19.13% |
| Bootstrap P(loss), 1000 samples | 14.10% |
| Profit concentration | 28.65% |

This candidate adds `trend_pullback` as a complementary long-biased signal on
top of the previous SOLUSDT 1h technical stack. It clears the standard
trade-count gate without breaking drawdown, bootstrap loss, or concentration
limits.

### Earlier Probe Candidate

| Metric | Value |
|---|---:|
| Full-period trades | 27 |
| Full-period win rate | 51.85% |
| Full-period return | 6.12% |
| Full-period max drawdown | 4.55% |
| Full-period Sharpe | 0.51 |
| WFO windows | 7 |
| Aggregate WFO trades | 17 |
| Mean OOS Sharpe | 0.74 |
| Compound OOS return | 8.98% |
| Bootstrap P(loss), 1000 samples | 23.50% |
| Profit concentration | 30.55% |

The candidate passed `probe_1h`:

```text
min_trades: 0
min_wfo_trades: 15
min_wfo_sharpe: 0.5
max_drawdown_pct: 10.0
max_bootstrap_p_loss_pct: 25.0
min_oos_return_pct: 0.0
max_profit_concentration_pct: 50.0
```

It failed only this standard gate:

```text
min_wfo_trades failed (17 < 20)
```

## Search Campaigns

All campaigns used local TimescaleDB indicator data and the canonical
`scripts/run_autoresearch.py` wrapper.

| Campaign | Result |
|---|---:|
| SOLUSDT 4h broad config search | 0 / 21 passes |
| SOLUSDT 4h aggregator focus | 0 / 40 passes |
| SOLUSDT 4h combined focus | 0 / 60 passes |
| SOLUSDT 4h near-pass expansion | 0 / 80 passes |
| BTCUSDT 4h combined focus | 0 / 40 passes |
| ETHUSDT 4h combined focus | 0 / 40 passes |
| BNBUSDT 4h combined focus | 0 / 40 passes |
| AVAXUSDT 4h combined focus | 0 / 40 passes |
| BNBUSDT 1h combined focus | 0 / 30 passes |
| SOLUSDT 1h combined focus | 0 / 30 standard passes |
| SOLUSDT 1h near-pass expansion | 0 / 100 standard passes |
| SOLUSDT 1h standard-gate bridge | 0 / 30 standard passes |
| SOLUSDT 1h near-miss trade lift | 0 / 30 standard passes |
| SOLUSDT 1h trend-pullback overlay | 2 / 30 standard passes |

The SOLUSDT 1h combined-focus campaign produced the best standard-gate near
miss:

```text
OOS return: 8.98%
OOS Sharpe: 0.74
Max DD: 4.55%
Bootstrap P(loss): 23.50% after 1000-sample validation
Profit concentration: 30.55%
WFO trades: 17
```

The later SOLUSDT 1h standard-gate bridge campaign did not beat that standard
score. Its best candidate improved return quality but became too sparse in OOS
walk-forward windows:

```text
Campaign artifact: /tmp/crypto-agent-autoresearch-sol-1h-standard-bridge-local-escalated-30
Best bridge score: -7.0
Full trades: 21
WFO trades: 13
OOS return: 10.28%
OOS Sharpe: 0.95
Max DD: 4.61%
Bootstrap P(loss): 19.00%
Profit concentration: 27.26%
Failure: min_wfo_trades failed (13 < 20)
```

The bridge campaign confirms the current tradeoff: moving far enough to reach
20+ WFO trades usually raises bootstrap loss and weakens Sharpe, while stricter
high-quality variants remain below the standard WFO trade-count gate.

The near-miss trade-lift campaign reproduced the best standard near miss but
did not solve the WFO trade-count gate:

```text
Campaign artifact: /tmp/crypto-agent-autoresearch-sol-1h-near-miss-lift-local-escalated-30
Best lift score: -3.0
Full trades: 27
WFO trades: 17
OOS return: 8.98%
OOS Sharpe: 0.74
Max DD: 4.55%
Bootstrap P(loss): 22.00% with 100 samples
Profit concentration: 30.55%
Failure: min_wfo_trades failed (17 < 20)
```

This reinforces the current conclusion: the existing indicator/aggregator surface
has a narrow robust pocket around 16-17 WFO trades. Attempts to lift trade count
to 20+ on this surface usually admit lower-quality trades and break bootstrap
or Sharpe gates.

The trend-pullback overlay campaign changed the result by adding a complementary
trend-consistent entry source instead of loosening the same threshold surface:

```text
Campaign artifact: /tmp/crypto-agent-autoresearch-sol-1h-trend-pullback-overlay-local-escalated-30
Passing candidates: 2 / 30
Best validation artifact: /tmp/crypto-agent-autoresearch-tracked-sol-1h-trend-pullback-overlay/archive/experiment-autopilot-20260603-205536-773725-2bdb15-20260603-155544.json
Best standard score: 101922.767413
Full trades: 43
WFO trades: 26
OOS return: 19.13%
OOS Sharpe: 1.72
Max DD: 7.39%
Bootstrap P(loss): 14.10% with 1000 samples
Profit concentration: 28.65%
Result: standard gate PASS
```

## Interpretation

The original live-bleed problem was partly a parity and risk-control issue, but
the profitability search shows a separate strategy-edge constraint:

- Strict filters can produce low drawdown and positive OOS return, but they were
  too sparse on the original threshold-only surface.
- Loosening thresholds increased trade count but worsened drawdown and bootstrap
  loss probability.
- Adding `trend_pullback` as a complementary signal added enough WFO trades while
  keeping risk gates intact.
- The strongest current candidate clears the standard research gate, but should
  still move only to paper/shadow validation before live promotion.

## Recommendation

Treat the `SOLUSDT 1h trend-pullback overlay` candidate as the current paper
validation candidate. It clears the standard WFO/bootstrap gate, but live
promotion still requires paper/shadow observation and operator review.

The tracked config is intentionally paper/probe-safe by default:

```text
mode: paper
trading_execution.enabled: false
futures.enabled: false
futures.test_mode: true
```

## Operational Status

The standard-gate candidate is wired as an opt-in production paper service in
`docker-compose.prod.yml`:

```text
agent_sol_1h_trend_pullback_overlay_paper
AGENT_ID=sol-1h-trend-pullback-overlay-paper
SETTINGS_PATH=config/settings.sol_1h_trend_pullback_overlay_paper.yaml
```

The service remains commented out by default. Enable it only for paper/shadow
validation. When enabled, also add a matching target to
`config/prometheus/agents.json`; while it is disabled, leaving it out prevents
false down-target alerts.

The runtime risk manager will load the dedicated guardrails automatically from:

```text
config/risk.sol-1h-trend-pullback-overlay-paper.yaml
```

because the filename matches the agent id.
