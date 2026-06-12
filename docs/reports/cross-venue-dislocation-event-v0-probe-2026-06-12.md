# cross-venue-dislocation-event-v0 — Gate 1 probe review (2026-06-12)

Reviewer verification of the first real run of
`scripts/probe_dislocation_event_strategy.py` (manifest-driven, `--execute`,
guard action RUN_CHEAP_PROBE allowed). Probe completed rc=0 in 115.4s.

- Command: 3 symbols (BTC/ETH/SOL) x 2 venues (binance_usdm, bybit), 1h,
  2024-01-01 -> 2026-06-12, fee 0.08% + slippage 0.02%, horizons 6/12/24,
  threshold mode `both` (fixed abs-bps grid 3.5/4.5/5.5/7.0 and trailing
  90d rolling tails 5/10%), cluster dedup + cooldown = horizon.
- Evidence: `research/rbi_loop/cross-venue-dislocation-event-v0/probe-verdict.json`

## Verdict: HAS_PULSE — independently confirmed

The pass list was re-derived from `per_scenario_stats` against the Gate 1
criteria (n>=40 deduped, net mean>0 AND median>0, win>=45%, monthly
concentration<=50%, no-lookahead mode): **exact match, 17/17 scenarios**.

| scenario (all long) | n | net mean % | net med % | win % | conc % |
|---|---|---|---|---|---|
| ETH neg basis fixed abs4.5 h6 | 150 | +0.352 | +0.267 | 56.0 | 24.5 |
| ETH neg basis fixed abs4.5 h12 | 113 | +0.649 | +0.476 | 58.4 | 16.3 |
| ETH neg basis fixed abs4.5 h24 | 88 | +0.671 | +0.587 | 55.7 | 39.4 |
| ETH neg basis fixed abs5.5 h6 | 86 | +0.308 | +0.107 | 58.1 | 33.1 |
| ETH neg basis fixed abs5.5 h12 | 69 | +0.391 | +0.168 | 55.1 | 42.5 |
| ETH pos basis fixed abs5.5 h24 | 49 | +0.932 | +0.737 | 61.2 | 30.7 |
| SOL pos basis fixed abs4.5 h12 | 174 | +0.405 | +0.195 | 54.6 | 45.1 |
| SOL pos basis fixed abs4.5 h24 | 146 | +0.630 | +0.658 | 58.9 | 41.1 |
| SOL pos basis fixed abs5.5 h12 | 104 | +0.729 | +0.252 | 54.8 | 45.9 |
| SOL pos basis fixed abs5.5 h24 | 88 | +1.047 | +0.587 | 58.0 | 41.1 |
| SOL pos basis fixed abs7.0 h24 | 42 | +1.790 | +2.145 | 61.9 | 34.1 |
| SOL neg basis fixed abs7.0 h12 | 74 | +0.546 | +0.127 | 52.7 | 46.9 |
| SOL pos basis rolling tail5 h12 | 528 | +0.163 | +0.118 | 51.1 | 48.6 |
| SOL pos premium rolling tail5 h6 | 705 | +0.155 | +0.034 | 50.8 | 43.4 |
| SOL pos premium rolling tail5 h24 | 408 | +0.348 | +0.123 | 52.9 | 28.6 |
| SOL pos premium rolling tail10 h12 | 838 | +0.127 | +0.040 | 50.8 | 39.9 |
| SOL pos premium rolling tail10 h24 | 549 | +0.205 | +0.114 | 51.5 | 38.0 |

## Drift check (the decisive test)

All 17 passes are long-only, so the reviewer computed the unconditional long
baseline on the identical joined dataset: enter long every non-overlapping h
bars, net of the same 0.10% cost.

| symbol | h6 net mean | h12 net mean | h24 net mean |
|---|---|---|---|
| BTCUSDT | -0.091% | -0.082% | -0.064% |
| ETHUSDT | -0.097% | -0.094% | -0.087% |
| SOLUSDT | -0.094% | -0.090% | -0.081% |

Always-long loses money net of costs at every symbol and horizon (win rates
43.5–49.1%). The passing event scenarios run +0.13% to +1.79% net — the edge
is conditional on dislocation events, not market drift.

## Kill criteria: none met

1. Dedup collapse <40 everywhere — no (n = 42–838 across 17 passes).
2. Net <= 0 everywhere — no.
3. Lookahead-only artifact — no (passes in both no-lookahead modes; strongest
   are fixed-grid, trivially no-lookahead).
4. >50% single-month P&L — no (max 48.6%).
5. Direction flips across symbols with no consistent rule — no formal flip
   (shorts never pass anywhere; the consistent rule is "dislocation event of
   either sign -> long", which beats a negative baseline).

## Caveats carried into Gate 2

- **Two-sided premise not confirmed.** The brief expected extreme_negative ->
  short entries; shorts pass nowhere. The family design should be long-only
  (or treat shorts as a falsifiable add-on), dropping the "independent
  directionality" rationale.
- **ETH and SOL condition on opposite extremes** (ETH: negative basis -> long
  recovery; SOL: positive basis -> long continuation). A single-rule family
  must either pick "either-sign extreme -> long" or accept per-symbol sign,
  which costs a free parameter.
- **Concentration headroom is thin on SOL**: 4 scenarios within 5pp of the 50%
  gate. WFO folds in Gate 2 will stress this.
- **BTC has zero passes** — consistent with the parent v1 probe; the family
  should target SOL (and optionally ETH), not BTC.

## Lane advance

`probe_verdict: HAS_PULSE` set in the manifest. Next guard action is
RUN_AUTORESEARCH, which requires the `dislocation_event_entry` family to be
implemented first (standalone event entry, not an overlay) under its own
reviewed plan — that plan must address the caveats above before any sweep.
