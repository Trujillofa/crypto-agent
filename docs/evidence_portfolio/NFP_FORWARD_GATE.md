# NFP Good-News-Is-Good — Forward Confirmation Gate

**Status:** IN FORCE — signed 2026-07-21
**Follows from:** probe #1 verdict **YES** (`NFP_PREREG.md`, 2026-07-16, PR #154)
**What this is:** the pre-registered *next gate* the YES bought. It is a
measurement protocol, not a build. No paper agent, no live risk, no capital.
**What this is not:** a new edge probe. The probe budget is closed
(`FEE_MARGINAL_PREREG.md` = DELETED_NOT_NAMED; no third probe).

---

> Rules below are fixed before any forward print is viewed. Editing any rule after
> a print has occurred invalidates the affected decision (default: CLOSE).

## Rule under test (identical to the OOS probe — nothing retuned)

- BTCUSDT spot, 1h candles, Binance.
- On each scheduled US NFP release: if release-time `surprise = actual − consensus > 0`
  (hot), enter long at the close of the first 1h candle at or after the release
  timestamp. If `surprise ≤ 0`, no position.
- Exit at the first candle close at or after entry + 24h. No stop, no target.
- Costs: 0.12% round trip (0.04%/side taker + 0.02%/side slippage).
- For reporting continuity, `z = surprise / 220.28` (the frozen population stdev of
  the 33 committed OOS surprises). The entry condition `surprise > 0` is equivalent
  to `z > 0`; the frozen divisor exists only so forward z values are point-in-time
  computable and comparable to the OOS table.

## Forward window and sample

- **Clean forward prints = scheduled NFP releases after this document is signed.**
  First eligible print: **2026-08-07**.
- Releases between the in-sample end (2026-05-08) and the sign date are recorded as
  *pre-lock interlude* rows for context only; they carry **zero verdict weight**.
- **Decision point: after 8 hot-surprise trades or 14 prints, whichever comes
  first** (~12–14 months at ~12 prints/year). One interim kill check at 7 prints.

## Per-print evidence (collected per `NFP_GOOD_NEWS_OOS_SOURCES.md` discipline)

- Consensus: Investing.com NFP forecast captured **before** the release — trigger a
  Wayback "Save Page Now" of
  `https://www.investing.com/economic-calendar/nonfarm-payrolls-227` within 24h
  before the release; record the exact snapshot URL.
- Actual: BLS release-time headline number from the Employment Situation release.
- Append one row per print to `data/macro_events/nfp_good_news_forward.csv`
  (same columns as the OOS table). Rows are append-only; a committed row is never
  edited.
- A print whose pre-release consensus was not captured is recorded as
  `MISSED_CAPTURE` and excluded from the trade count; 3 or more missed captures
  before the decision point → the gate is decided on whatever sample exists at the
  original decision date (no extension).

## Pass gates (all required, evaluated once at the decision point)

- Net expectancy > 0 after 0.12% costs across forward hot-surprise trades.
- Profit factor ≥ 1.10.
- Removing the single best trade leaves net expectancy > 0.
- No parameter, window, or gate edits after sign-off.

**Pass →** escalate to an explicit human decision on a paper-live deployment,
which requires its own pre-registered gate before any config or code changes.
**Fail (or interim kill: expectancy ≤ 0 with ≥ 5 hot trades at the 7-print
check) →** the NFP family closes terminally; the OOS YES stands in the record as
a non-replicating result. No re-opening without a named changed input.

## Scope limits

- One forward CSV + the per-print collection routine. Any script Grok builds for
  this must be read-only with respect to production services.
- No optimization, no additional symbols in the verdict (ETH/SOL may be logged as
  consistency checks), no second event type, no intraday variants.

## Sign-off

Parameters locked by: Yderf, 2026-07-21
