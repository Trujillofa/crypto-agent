# Lane Brief — Cross-Asset / TradFi Risk-Regime Probe v0 (Gate 0)

**Status:** Gate 0 (brief) → Gate 1 (cheap probe) pending
**Author role:** planned by Claude (planner/reviewer); data-feasibility audit + cheap probe to be built by Grok (builder)
**Predecessors / evidence chain:**
- Trend family CLOSED: [daily-trend-breadth-probe-v0.md (report)](../reports/daily-trend-breadth-probe-v0.md) — only per-symbol edge is "long in uptrend" but it is **shared beta** (corr 0.59), not independent.
- News/event family CLOSED: [macro-surprise-drift-probe-v0.md (report)](../reports/macro-surprise-drift-probe-v0.md) — strongest (unclaimable) signal was **hot NFP → crypto UP**: crypto behaves as a **risk-on/off asset**.
- Research rules: [../reports/research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md)

---

## Why this lane (first principles — two results converge)

Every **crypto-internal** structure lane has failed (sweeps, ranges, trend, breadth, funding,
basis, dislocation, calendar). The only thing that ever made real PnL (`sentiment-macro-bot`) rode
**macro/sentiment risk-on**. And two independent probes now say the same thing:
1. the trend edge is **shared beta**, not an independent crypto signal;
2. crypto moves **with risk sentiment** (good NFP → crypto up).

Conclusion: crypto's edge-bearing information is likely **exogenous** — the TradFi risk regime —
not internal microstructure. This lane tests that directly, using the **risk complex**: equity-index
futures (risk-on proxy), the US dollar (DXY), rates (2Y/10Y), and volatility (VIX/MOVE).

## The trap this lane must avoid (state it loudly)

**Contemporaneous correlation is NOT an edge.** Crypto–Nasdaq correlation has been well-documented
and high since 2022; knowing crypto moves *with* equities *today* is not tradeable. The only
tradeable forms are **predictive**:
- **lead-lag** — a TradFi move that *precedes and predicts* a subsequent crypto move (or vice-versa
  via 24/7 price discovery), measured strictly point-in-time; or
- **regime conditioning** — the TradFi risk *state* (known as of time t) predicts crypto's *forward*
  drift.

If only contemporaneous co-movement exists with no forward predictability → **NO_PULSE**. That is
the bar, and the most likely (efficient-markets) outcome.

## The central data challenge — 24/7 crypto vs sessioned TradFi

Crypto trades 24/7; TradFi does not. This alignment is the crux (the analog of last lane's
point-in-time consensus). The probe must handle, explicitly and in UTC:
- **Session hours** — US equity RTH ≈ 14:30–21:00 UTC; equity-index *futures* (ES/NQ) trade ≈ 23h/5d;
  DXY/rates have their own conventions.
- **Weekends/holidays** — TradFi closed, crypto open. The Fri-close → Mon-open gap and the crypto
  weekend drift are first-class cases, not noise to drop.
- **No look-ahead** — any crypto window must start strictly **after** the TradFi observation it
  conditions on; never use a TradFi close/level not yet printed at the crypto entry time.

## STEP 0 — Data-feasibility audit (the gate, before any edge test)

1. **Source TradFi proxies** over 2024-01-01 → 2026-06-01 at usable granularity. `yfinance` is
   importable in this repo (free). Frozen proxy set (defined ex-ante — see below). Daily closes are
   the tractable floor; **intraday (1h)** is preferred where free history allows (Yahoo ~730d of 1h)
   — document what granularity is actually obtainable per proxy.
2. **Verify timestamp/session integrity** — record each series' close time + timezone, convert to UTC,
   and document weekend/holiday gaps. A series with ambiguous timestamps is a data-quality caveat.
3. **Coverage check** — need the frozen proxy set with ≥ ~2y of aligned observations vs BTC/ETH/SOL
   (existing prod 1h `ohlcv`). If a core proxy (the equity risk proxy especially) can't be sourced
   cleanly → record and either drop it (with the set still meaningful) or **BLOCKED_ON_DATA**.
4. Commit the pulled TradFi series to `data/tradfi/*.csv` with a README citing source + granularity +
   session notes (so the probe is reproducible without re-hitting Yahoo).

## Frozen proxy set (ex-ante — no indicator fishing)

Primary, fixed before any return is measured:
- **Equity risk:** Nasdaq-100 proxy — **NQ futures or QQQ** (crypto's tightest TradFi link).
- **US dollar:** **DXY** (or UUP).
- **Rates:** **US 10Y yield** (^TNX / FRED DGS10).
- **Volatility / fear:** **VIX** (^VIX).

Do **not** add more proxies (gold, oil, 2Y, credit spreads, individual stocks) after seeing results
to manufacture a pass. A pre-registered secondary set MAY be reported separately, clearly labelled,
never to rescue a failed primary.

## Edge thesis (hypotheses to probe — report separately)

- **H1 (lead-lag):** a defined TradFi move over window W (e.g. NQ return during the US session, or
  the last closed TradFi bar) predicts BTC/ETH/SOL forward return over the *next* crypto window
  {+6h / +24h}, beyond contemporaneous co-movement and beyond a matched random baseline.
- **H2 (regime conditioning):** the TradFi risk *state* as of time t — e.g. VIX level bucket, DXY
  trend (above/below its own SMA), 10Y direction — conditions crypto's forward drift with a
  consistent, theory-aligned sign (risk-on state → positive crypto drift; risk-off → negative).
- **H3 (weekend gap, secondary):** crypto's weekend move predicts the Monday equity-futures gap, or
  the Friday TradFi close predicts crypto's weekend drift. Report as a bonus, not the primary bar.

State the **expected sign ex-ante** per relationship (risk-on → crypto up; stronger DXY → crypto
down; higher 10Y → crypto down; VIX spike → crypto down) so a wrong-signed result is not relabelled.

## Cheap probe plan (read-only, after Step 0 passes)

1. Align TradFi series to crypto 1h bars in UTC, point-in-time (crypto window starts strictly after
   the TradFi observation; weekends/holidays handled explicitly).
2. **H1:** regress/sort crypto forward return on the prior TradFi move; compare to (a) the
   contemporaneous correlation and (b) a matched random-window baseline — the *forward* excess is
   what matters.
3. **H2:** bucket crypto forward returns by the TradFi regime state; test sign consistency across
   BTC/ETH/SOL and vs baseline.

## Pulse criteria (encode in the probe)

- **H1 PASS:** prior-TradFi-move → crypto forward return shows consistent expected-sign predictive
  relationship (rank correlation / bucket spread) beating the matched baseline past the fee/noise
  bar (state it, e.g. > 0.3% net) on **≥2 of 3 symbols** at **≥1 horizon** — and materially above the
  contemporaneous co-movement (i.e. genuinely *predictive*, not just correlated).
- **H2 PASS:** regime-bucketed forward drift is expected-signed and consistent across **≥2/3 symbols**.

Verdict:
- **HAS_PULSE** — H1 and/or H2 hold predictively in the expected direction → bounded standalone
  risk-regime strategy/filter surface, then Gate 2.
- **WEAK_EDGE** — relationship present but only one symbol/horizon, or right shape inside the fee bar,
  or only the (weaker) H2 regime filter holds → record, decide; do not add proxies to rescue it.
- **NO_PULSE** — only contemporaneous correlation, no forward predictability → close; crypto is
  efficiently co-priced with the risk complex and there is nothing to trade ahead of it.

## Expected failure modes (do not oversell)

- **Co-movement is contemporaneous and efficient** — no lead-lag after costs → NO_PULSE (most likely).
- **Regime-dependent correlation** — the crypto-equity link itself switches on/off across 2024-26;
  an in-sample relationship may not be stable (the recurring WFO-survival problem).
- **Intraday TradFi history too short/thin** free → forced to daily, which blurs lead-lag → at best H2.
- **Wrong/unstable sign** under "good-news-is-bad" vs "risk-on" regimes.

## Why this satisfies the allowed-next-family rules

| Rule (reset doc) | This lane |
|---|---|
| Different primitive | **Exogenous cross-asset risk complex** — not a crypto-OHLCV transform. |
| Cheap probe first | Read-only, free data, after a feasibility audit. |
| Standalone | Not attached to SOL overlay or sentiment-macro. |
| WFO-realistic count | Continuous conditioning → far more observations than the event lanes. |
| Independent directionality | Driven by TradFi state — and *predicted* by our own two strongest results. |

## Validation command plan

```bash
uv run python scripts/probe_cross_asset_risk_regime.py --json   # builder to create

uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/cross-asset-risk-regime-probe-v0.md \
  --probe-verdict <HAS_PULSE|WEAK_EDGE|NO_PULSE> --pretty
```

## Guardrails (do not violate)

1. **Predictive, not contemporaneous** — the headline metric is *forward* excess vs baseline AND vs
   the same-window co-movement. Never report contemporaneous correlation as the edge.
2. **Point-in-time, UTC, session/weekend-explicit** — crypto window starts strictly after the TradFi
   observation; no not-yet-printed TradFi level.
3. **Frozen proxy set + ex-ante signs** — no post-hoc proxy/horizon additions or sign-flips to rescue.
4. **Data audit is gate 0** — core proxy unobtainable cleanly → BLOCKED_ON_DATA.
5. Commit pulled TradFi series for reproducibility; document granularity + session handling honestly.

## Reviewer (Claude) checkpoints

(a) TradFi source + granularity + session/timezone handling documented; (b) **forward predictability
isolated from contemporaneous correlation** (the trap); (c) point-in-time alignment verified (no
not-yet-printed TradFi value, weekends explicit); (d) proxy set + signs frozen ex-ante; (e) results
per symbol/horizon vs matched baseline AND vs co-movement; (f) verdict honest with observation counts
and any correlation-regime-stability caveat.
