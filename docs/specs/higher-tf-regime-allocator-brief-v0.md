# Higher-TF Regime Allocator — Lane Brief v0

**Status:** Brief created; cheap probe (Gate 0/1) required before any strategy code, manifest, or autoresearch.
**Date:** 2026-06-14
**Related:** [research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md), [RBI_AUTORESEARCH_LOOP.md](../RBI_AUTORESEARCH_LOOP.md), [cross-venue-dislocation-event-strategy-v0.md](./cross-venue-dislocation-event-strategy-v0.md) (prior lane, closed post-probe).

---

## Lineage and what changed

Cross-venue (old #1) is closed (0/30 both variants per the sweep). Per the updated ranking in the 2026-06-06 reset, the next data-first lane is the higher-timeframe regime surface.

It is the cheapest to probe because regime features already exist — reuse them, do not build new ingestion:

- `migrations/007_add_regime_features.sql` columns on `indicators`: `ema_slope_50`, `volatility_percentile`, `atr_percentile`, `volume_regime`, `price_vs_weekly`, `price_vs_monthly`, `rsi_slope`, `trend_consistency`.
- Existing computation (causal/rolling windows in `src/features/technical.py`, forward-walking backfill in `scripts/backfill_regime_features.py`).
- Reader + MTF join infra: `src/features/reader.py` (strict no-lookahead `_join_timeframes` using regime bar close <= entry bar time; `fetch_multi_timeframe`).
- Prior usage: `src/strategy/regime_router.py`, `src/strategy/multi_timeframe_regime.py`.

The surface tests whether a 4h (or 1d) regime label, observed at 1h bar t with only data ≤ t, carries statistically detectable forward-return information for the live agent's base timeframe (1h), net of fees + slippage.

**HYP-HTFR-001:** A higher-timeframe (4h and 1d) regime label carries forward-return information for SOL 1h (the live agent's timeframe), net of fees+slippage, computed without lookahead. If "favorable" regime bars have materially better forward expectancy than "unfavorable" bars — with adequate sample and robustness across symbols — the surface has a pulse and justifies autoresearch later.

## Why this is allowed under the reset rules

1. **Different primitive** — HTF regime state (computed from existing trailing features on 4h/1d), not a 1h OHLCV shape, session label, cross-venue event, or filter attached to the promoted SOL overlay.
2. **Cheap probe first** — read-only probe defined below using already-populated columns and reader join; HAS_PULSE (Gate 1) required before any strategy class, family, or autoresearch manifest.
3. **No new ingestion** — all required columns are in `indicators` (backfilled for supported TFs/symbols); OHLCV provides closes for forward returns.
4. **Direct test of existing infra value** — validates whether the regime features already computed and used in `regime_router`/`multi_timeframe_regime` actually predict returns out of sample (the prior regime work produced candidate WFOs that did not promote; this isolates the predictive content of the label itself).
5. **Post-pulse optionality preserved** — the probe itself does not decide usage shape.

Banned shapes remain banned: no new filters/gates on the SOL 1h trend-pullback stack, no re-running closed regime overlay campaigns, no parameter iteration on prior MTF regime WFOs.

## Data

Already in production: regime feature columns on `indicators` hypertable for 1h/4h (and potentially 1d where backfilled) across BTC/ETH/SOL and others, 2024-01 → present. OHLCV at 1h (and higher TFs).

**Gate 0 (data coverage):** the probe must report, per symbol × regime-TF, the count of 1h bars that have non-NULL regime labels from the higher TF (after no-lookahead join). Low coverage (e.g. <<200 labeled bars per bucket) is a coverage failure, not a modeling failure.

**No new ingestion is required for the probe.** 1d regime features are used where present; if coverage on 1d is sparse the probe will naturally surface it via bucket counts.

## Gate 1 — Sharpened cheap probe (the deliverable of this lane's first step)

New read-only script `scripts/probe_higher_tf_regime.py`.

Contract (exact mirror of `probe_dislocation_event_strategy.py`):

- argparse CLI with the specified defaults: `--symbols SOLUSDT,BTCUSDT,ETHUSDT`, `--base-timeframe 1h`, `--regime-timeframes 4h,1d`, `--start 2024-01-01`, `--fee-pct 0.08`, `--slippage-pct 0.02`.
- `--verdict-output research/rbi_loop/higher-tf-regime-allocator-v0/probe-verdict.json` (guard-consumable JSON with keys: `verdict`, `note`, `passing_scenarios`, `per_bucket_stats` (or equivalent), `thresholds`, `generated_at`/`timestamp`, `config`).
- Verdict ∈ {HAS_PULSE, WEAK_EDGE, NO_PULSE}.
- `--smoke` no-DB path that always returns NO_PULSE (and still writes well-formed verdict JSON); used by unit tests.
- Logging via `get_logger` from `src/utils/logger.py`; `print(...)` only for the final human-readable report block (and the single "Wrote verdict to ..." line). No bare `print` elsewhere, no `logging` module directly.
- Reads regime features exclusively via `src/features/reader.py` (specifically `fetch_multi_timeframe` for the no-lookahead join of base 1h bars with higher-TF regime indicators suffixed `_4h` / `_1d`).
- No lookahead: the reader join guarantees that a regime label attached to a 1h bar at time t uses only a completed higher-TF bar whose close time ≤ t. Stored regime columns themselves are trailing (computed in `compute_indicators` on prefix windows; backfill walks i using data[0:i+1]).
- If any stored column were full-sample (it is not), the probe must detect and recompute a trailing version internally — but none are.
- Fixed small forward horizon (default 6 × 1h bars) for expectancy measurement. Net return = signed gross move − (fee_pct + slippage_pct).
- Pure helper `compute_bucket_expectancy` (or equivalent) for the forward-bucket arithmetic; this is unit-tested in isolation on synthetic price + label series (no DB).
- Produces per-(symbol, regime_tf) bucket stats (favorable vs unfavorable counts, mean/median net fwd, win rate / P(loss), simple Sharpe proxy = mean/std of the per-bar net fwds or equivalent), delta between buckets, and whether the Gate 1 numeric thresholds are met for that scenario.
- No `--execute`, no live paths, no order logic.

**HAS_PULSE requires all (as CLI defaults / hard thresholds in code):**

1. **Sample:** each regime bucket (favorable / unfavorable) has ≥ 200 base-timeframe bars over the window for that (symbol, regime_tf) (not a sparse artifact).
2. **Separation:** favorable-bucket mean forward return (net of fees+slippage, at the fixed horizon) exceeds unfavorable by ≥ 15 bps (0.15%), **and** favorable forward-return Sharpe > 0 while unfavorable ≤ 0 (or favorable P(loss) is ≥ 8 percentage points lower than unfavorable).
3. **Robustness:** the separation (same sign, ≥ half the magnitude) holds for ≥ 2 of the 3 symbols, **or** across two non-overlapping sub-periods within the primary symbol's series.

**WEAK_EDGE** if (2) holds for at least one (symbol, regime_tf) but (1) or (3) fails; else **NO_PULSE**.

The probe reports passing_scenarios as the (symbol, regime_tf, horizon) combos (or bucket pairs) that individually satisfy the numeric parts of (2), then applies the aggregate (1)+(3) rules for the top-level verdict. All evaluation on net numbers; gross reported for transparency in the JSON stats.

## Kill criteria (close at probe, cheaply)

- Bucket counts < 200 in favorable or unfavorable for every symbol × regime_tf → coverage or labeling too sparse; close.
- No separation meeting the 15 bps + (Sharpe sign or P(loss) 8pp) delta on net returns anywhere → no detectable regime-conditioned drift; close.
- Edge only appears with full-sample (lookahead) regime labels (probe must not use them; if stored columns required recompute the probe would flag) → artifact; close.
- Separation present on only one symbol and fails sub-period split → non-robust; close (WEAK_EDGE at best).
- Concentration: fwd returns in the favorable bucket dominated by a handful of bars/months (e.g. >50% of positive net P&L from one episode) → single-regime story; surface as caveat but not automatic close.

## Gates 2–6 (RBI-aligned, unchanged from house rules)

- **Gate 2:** Bounded autoresearch family (new `higher_tf_regime` family) only after HAS_PULSE. Standard gate profile. The family would be either (A) or (B) below — decision deferred.
- **Gate 3/4:** Promotion pre-filter, then bootstrap=1000.
- **Gate 5:** `analyze_entry_overlap.py` against the live SOL agent (and sentiment-macro) if a standalone path is chosen (target <35% Jaccard).
- **Gate 6:** ≥20 closed paper trades under a distinct AGENT_ID before any live notional (or risk budget allocation if path A).

Human approval required at every stage transition; the lane manifest guard (`rbi_loop_from_manifest.py`, dry by default) will record each decision **after** this Gate 1 work.

## Post-pulse fork (explicit — not decided by the probe)

After HAS_PULSE (if it occurs), the eventual implementation could be:

- **(A)** A portfolio risk allocator / gate on existing agents: only allow/enlarge risk when the HTF regime label (read via reader at runtime) is "favorable". This would be an overlay-style risk layer, not a new entry generator.
- **(B)** A standalone higher-TF regime agent (candidate #2) that generates its own entries conditioned on the 4h/1d regime state (re-using or extending the multi_timeframe_regime patterns).

The cheap probe (Gate 1) is identical for both and does not choose. The choice, plus any manifest/config, is for later human + reviewer decision after the verdict and a short report. **This probe change must not create any config/autoresearch/rbi_loop.*.yaml, must not touch autoresearch_loop.py or related, must not produce strategy code.**

## Stop / close rules

- Probe NO_PULSE or fails kill criteria → close lane, record verdict + short report, no further work on this surface.
- Any implementation needing > few free parameters (or complex labeling) to pass gates → close.
- Evidence the regime signal is a pure proxy for existing sentiment-macro or trend-pullback entries (high overlap) → close (defer to those lanes).

Goal: a cheap, data-driven test of whether the already-computed higher-TF regime features contain usable forward information. If yes, one more potential independent or risk-control surface. If no, close cleanly with evidence.

This completes Gate 0 + Gate 1 only. Hard stop for Claude/human review.
