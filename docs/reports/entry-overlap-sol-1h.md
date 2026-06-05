# Entry overlap report

- Symbol: `SOLUSDT`
- Timeframe: `1h`
- WFO: train=3mo test=2mo
- Tolerance: 1h

## OOS entry counts (backtest)

| Agent | Symbol | Entries | Note |
|-------|--------|---------|------|
| sol_1h_trend_pullback_overlay_live | SOLUSDT | 32 |  |
| sentiment_macro | SOLUSDT | 2 |  |
| sol_1h_trend_pullback_overlay_paper | SOLUSDT | 36 | Second SOL 1h config variant (same signal stack as live) |

## Pairwise overlap (OOS backtest)

| A | B | Shared | Jaccard | %A in B | %B in A |
|---|---|---:|---:|---:|---:|
| sol_1h_trend_pullback_overlay_live | sentiment_macro | 0 | 0.00% | 0.0% | 0.0% |
| sol_1h_trend_pullback_overlay_live | sol_1h_trend_pullback_overlay_paper | 31 | 83.78% | 96.9% | 86.1% |
| sentiment_macro | sol_1h_trend_pullback_overlay_paper | 0 | 0.00% | 0.0% | 0.0% |

## Pairwise overlap (live DB)

| A | B | Shared | Jaccard | %A in B | %B in A |
|---|---|---:|---:|---:|---:|
| live:sol-1h-trend-pullback-overlay-live | live:sentiment-macro-bot | 0 | 0.00% | 0.0% | 0.0% |

## Interpretation

Low OOS entry overlap between SOL 1h overlay and sentiment-macro. A second SOL 1h agent may add diversification if it passes promotion gates independently.
