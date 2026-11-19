# NFP Good-News-Is-Good — Forward Confirmation Gate v2

**Status:** DRAFT — pending human signature (do not merge until signed)
**Follows from:** v1 forward confirmation gate (`NFP_FORWARD_GATE.md`, signed 2026-07-21, closed at 3 missed captures on 2026-10-02 per its own rule).
**Named changed input:** automated, redundant consensus capture whose primary source is the MT5 economic calendar's `forecast` logged point-in-time by mt5-arch-integration **MT-2** (`CalendarValueLast` change log rsynced from the MT5 desktop to `/opt/shared/calendar_pit/<broker>/*.jsonl`), with Investing.com/Wayback/archive.ph as an independent cross-check.
**What this is:** the measurement protocol for forward confirmation under the redundant consensus capture architecture. No paper agent, no live risk, no capital.
**What this is not:** a new edge probe or threshold retune. Parameters, entry/exit rules, and pass gates are identical to v1.

---

> Rules below are fixed before any forward print is viewed under this v2 gate. Editing any rule after
> a print has occurred invalidates the affected decision (default: CLOSE).

## Rule under test (identical to v1 and OOS probe — copied verbatim, nothing retuned)

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

## Consensus source definition (v2 named changed input)

- **Primary consensus source:** Point-in-time consensus forecast extracted via `scripts/nfp_consensus_from_mt5.py` from the MT-2 calendar PIT log (`/opt/shared/calendar_pit/<broker>/*.jsonl`). The last record logged strictly *before* the scheduled release timestamp (08:30 America/New_York) is authoritative.
- **Cross-check:** Investing.com pre-release snapshot via Wayback or archive.ph captured within 24h before release.
- **Fail-closed:** If the MT5 PIT log contains no pre-release change for the print, the print cannot be scored from retroactive data.

## Forward window and sample

- **Clean forward prints = scheduled NFP releases after this document is signed by human.**
- Decision point: after 8 hot-surprise trades or 14 prints, whichever comes first (~12–14 months at ~12 prints/year). One interim kill check at 7 prints.

## Per-print evidence

- Actual: BLS release-time headline number from the Employment Situation release.
- Append one row per print to `data/macro_events/nfp_good_news_forward_v2.csv`. Rows are append-only; a committed row is never edited.
- A print whose pre-release consensus was not captured is recorded as `MISSED_CAPTURE` and excluded from the trade count; 3 or more missed captures before the decision point → the gate is decided on whatever sample exists at the original decision date (no extension).

## Pass gates (all required, evaluated once at the decision point — verbatim from v1)

- Net expectancy > 0 after 0.12% costs across forward hot-surprise trades.
- Profit factor ≥ 1.10.
- Removing the single best trade leaves net expectancy > 0.
- No parameter, window, or gate edits after sign-off.

**Pass →** escalate to an explicit human decision on a paper-live deployment, which requires its own pre-registered gate before any config or code changes.
**Fail (or interim kill: expectancy ≤ 0 with ≥ 5 hot trades at the 7-print check) →** the NFP family closes terminally; the OOS YES stands in the record as a non-replicating result. No re-opening without a named changed input.

## Scope limits

- No backfilling v1 rows from MT5 history.
- No threshold tuning, no additional symbols in the verdict (ETH/SOL may be logged as consistency checks only), no second event type.

## Sign-off

Parameters locked by: [PENDING HUMAN SIGNATURE - DO NOT MERGE]
Date:
