# Lane Brief — Synthetic-Index Structural Edge Probe v0

**Status:** Gate 0 brief; read-only Gate 1 probe.
**Source:** User-supplied shortlist of ten Deriv synthetic indices.
**Script:** `scripts/probe_synthetic_indices.py`.

## Requirement made testable

The source claim is not yet an edge: engineered market behavior does not imply a profitable,
predictive signal. This probe converts each advertised behavior into one frozen, falsifiable
hypothesis and tests it on public Deriv candles:

| Instruments | Frozen hypothesis | Signal | Horizon |
|---|---|---|---:|
| Volatility 50/75/100 | short-horizon momentum | sign of trailing 15-minute return | 15m |
| Crash 500/1000 | non-spike upward drift | long while the current 1m return is above the calibration 1% tail | 5m |
| Boom 500/1000 | non-spike downward drift | short while the current 1m return is below the calibration 99% tail | 5m |
| Step Index | mechanical one-step mean reversion | fade the prior 1m move | 1m |
| Jump 100 | post-jump continuation | follow a move above the calibration 99th absolute-return percentile | 5m |
| Range Break 100 | range-break continuation | follow a close beyond the trailing 60m high/low | 15m |

All observations are sampled at non-overlapping horizon intervals. No parameter sweep is permitted
in v0.

## Why this is a separate lane

The closed crypto research program tested exchange-traded crypto prices and related public
telemetry. Deriv synthetic indices are a different generator and universe with deliberately
engineered statistical profiles. That distinction makes a cheap falsification probe reasonable,
but it is **not a differentiated information advantage**: the generator is proprietary, the data
is public, and the advertised behavior is known to every participant.

This PR therefore stops at Gate 1. It must not register a strategy, alter an agent configuration,
or imply deployment readiness.

## Data and frozen universe

- Provider: Deriv public WebSocket market-data API; no account or trading credential.
- Discovery: `active_symbols`; match exact normalized display names rather than stale hard-coded
  short symbols.
- History: `ticks_history`, one-minute candles.
- Universe: exactly the ten instruments in the source shortlist.
- Current API compatibility: the response uses `underlying_symbol` and
  `underlying_symbol_name`; the original Step instrument is exposed as `Step Index 100`
  (`stpRNG`).
- Window: latest 30 complete UTC days by default.
- Split: first 40% calibration, final 60% forward test.
- Minimum forward observations: 100 per instrument, except sparse Jump/Range-Break events (30).
- Missing any requested instrument is `BLOCKED_ON_DATA` unless a diagnostic
  `--allow-partial-universe` run is explicitly requested.

## Gate 1 controls

Each instrument is one registered test. `HAS_PULSE_COST_PROXY` requires all of:

1. Positive calibration and forward gross oriented return.
2. Forward mean exceeds a thesis-destroying block-sign null with at least 1,000 resamples.
3. Holm-adjusted `p_adj < 0.05` across all available instrument tests.
4. No UTC day contributes more than 25% of absolute oriented edge.
5. Mean forward return remains positive after the frozen 20 bps round-trip cost proxy.
6. Family breadth:
   - at least 2/3 Volatility instruments pass;
   - at least 2/4 Crash/Boom instruments pass;
   - singleton families pass both halves of their forward sample.

Block-sign randomization preserves within-block return magnitudes and local volatility while
destroying systematic directional alignment. The random seed is fixed and reported.

`WEAK_EDGE` means some gross statistical structure exists but one or more economic/durability
gates fail. `NO_PULSE` means no instrument clears the statistical null. `BLOCKED_ON_DATA` means the
frozen universe or minimum sample requirement could not be evaluated.

## Cost and execution limitation

The public historical endpoint supplies indicative candles, not executable Deriv MT5/cTrader CFD
bid/ask quotes, commissions, slippage, financing, or contract sizing. The default 20 bps
round-trip value is a **screening proxy**, not an empirical execution model.

Consequently even `HAS_PULSE_COST_PROXY` cannot advance to strategy implementation. Promotion
requires a separate venue-specific cost study using executable demo quotes and contract
specifications, followed by forward paper execution.

## Expected failure modes

- The engineered distribution is real but has no conditional predictability.
- Apparent returns vanish under the block-sign null or Holm correction.
- Returns are concentrated in one synthetic event/day.
- Gross structure is smaller than executable spread and slippage.
- Candle aggregation hides tick-level spike behavior.
- Public API symbols or history availability differ from the supplied shortlist.

The 2026-06-27 discovery audit found Range Break 100 in Deriv's current CFD product/commission
listing but not in the public Options `active_symbols` response. The strict ten-instrument verdict
therefore remains `BLOCKED_ON_DATA`; the other nine instruments are still evaluated and reported
without substituting a different symbol.

## Independence and operational constraints

- Expected independence from current Binance agents is high at the return-series level.
- Operational independence is low: this repository has no Deriv execution adapter.
- `strategy.global_trend_filter_enabled`: **false / not applicable**. The probe does not use the
  strategy engine or inherit `base.yaml`; applying a crypto EMA200 gate would change the frozen
  family hypotheses.
- No API tokens, account actions, order proposals, or trades.

## Validation commands

```bash
uv run pytest tests/test_probe_synthetic_indices.py -v
uv run ruff check scripts/probe_synthetic_indices.py tests/test_probe_synthetic_indices.py
uv run ruff format --check scripts/probe_synthetic_indices.py tests/test_probe_synthetic_indices.py
uv run python scripts/probe_synthetic_indices.py \
  --days 30 \
  --bootstrap-resamples 2000 \
  --report docs/reports/synthetic-index-structural-edge-probe-v0.md
```

After the live data run, the generated report must record symbol resolution, candle coverage,
calibration/forward sample counts, gross and proxy-net edge, null percentile, `p_adj`, daily
concentration, family breadth, and the terminal Gate-1 verdict.

## Primary references

- [Deriv Active Symbols API](https://developers.deriv.com/docs/data/active-symbols/)
- [Deriv Ticks History API](https://developers.deriv.com/docs/data/ticks-history/)
- [Deriv public market-data workflow](https://developers.deriv.com/docs/workflows/)
- [Deriv synthetic-index family overview](https://blog.deriv.com/blog/synthetic-indices-overview-volatility-crash-boom-jump)
- [Deriv CFD instrument commissions](https://deriv.com/partners-help-center-questions/what-are-the-commission-rates-for-trades-on-the-deriv-ctrader-account)
