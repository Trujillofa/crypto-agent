# NFP Good-News-Is-Good — Forward Confirmation Gate v2

**Status:** DRAFT — pending human signature (do not merge until signed). Not in force;
governs no print until signed below. Signature is deferred until the MT-2 PIT source
exists (`docs/reports/adversarial-audit-nfp-capture-2026-11-19.md`).
**Follows from:** v1 forward confirmation gate (`NFP_FORWARD_GATE.md`, signed 2026-07-21,
listed as SIGNED, in force in `DAY30_REVIEW.md`). v1 has recorded 3 `MISSED_CAPTURE`
prints (2026-08-07, 2026-09-04, 2026-10-02). v1's own text governs what that means:
"3 or more missed captures before the decision point → the gate is decided on whatever
sample exists at the original decision date (no extension)." v1 is not closed or edited
by this document; prints up to v2's first eligible print remain v1 prints under v1's text.
**Named changed input:** automated, redundant consensus capture whose primary source is the MT5 economic calendar's `forecast` logged point-in-time by mt5-arch-integration **MT-2** (`CalendarValueLast` change log rsynced from the MT5 desktop to `/opt/shared/calendar_pit/<broker>/*.jsonl`), with Investing.com/Wayback/archive.ph as an independent cross-check.
**What this is:** the measurement protocol for forward confirmation under the redundant consensus capture architecture. No paper agent, no live risk, no capital.
**What this is not:** a new edge probe or threshold retune. Parameters, entry/exit rules, and pass gates are identical to v1.

---

> Rules below are fixed before any forward print is viewed under this v2 gate. Editing any rule after
> a print has occurred invalidates the affected decision (default: CLOSE).

Rules marked **[DRAFT CHOICE — HUMAN CONFIRMS AT SIGNATURE]** were drafted by a builder as the
strict option. Signing this document confirms each of them as written; to choose otherwise,
edit the rule before signing, never after a v2 print is viewed.

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

- **Primary consensus source:** the pre-release forecast printed by
  `scripts/nfp_consensus_from_mt5.py` (exit 0) from the MT-2 calendar PIT log
  (`/opt/shared/calendar_pit/<broker>/*.jsonl`). The last change logged strictly
  *before* the scheduled release timestamp (08:30 America/New_York, converted from
  trade-server time to UTC) is authoritative. The reader fails closed (non-zero exit) on a
  missing, `LONG_MIN`, null, NaN or unparseable latest change; on broker disagreement;
  on torn JSONL lines; and on any schema mismatch.
- **Reader parameters, fixed at signature** (filled by the signer; a print cannot be
  scored while any is blank):
  - MT5 `event_id` of the US Nonfarm Payrolls **headline** (not Private Nonfarm
    Payrolls): `[FILL AT SIGNATURE]`
  - Broker directories read under `/opt/shared/calendar_pit/`: `[FILL AT SIGNATURE]`
  - Trade-server UTC offset per broker at the release instant: `[FILL AT SIGNATURE]`
- **Cross-check:** Investing.com pre-release snapshot via Wayback or archive.ph captured
  within 24h before release. It never substitutes for the primary source.
- **MT5 PIT missing or reader non-zero exit → `MISSED_CAPTURE` under v2**, even when the
  Investing.com cross-check was captured. The cross-check value and snapshot URL are
  logged on the row as context only. No print is scored from retroactive data.
  **[DRAFT CHOICE — HUMAN CONFIRMS AT SIGNATURE]**
- **Exact-match consensus required:** the MT5 forecast and the Investing.com cross-check
  forecast, both in thousands of jobs, must be numerically equal. Any disagreement →
  `MISSED_CAPTURE`, with both values (and their sources) logged on the row. Broker-to-broker
  disagreement inside the MT5 log is already a reader failure → `MISSED_CAPTURE`.
  **[DRAFT CHOICE — HUMAN CONFIRMS AT SIGNATURE]**
- **MT5 present, cross-check absent** (no Investing.com snapshot inside the 24h window) →
  the print is scored from MT5, and the row records that the cross-check is absent. The
  stricter alternative (cross-check absent → `MISSED_CAPTURE`) would make the gate depend
  on the same single Wayback path that produced v1's three misses.
  **[DRAFT CHOICE — HUMAN CONFIRMS AT SIGNATURE]**
- **Multiple snapshots inside the 24h window:** the latest pre-release capture is
  authoritative (for MT5, the reader's latest pre-release change; for the cross-check, the
  latest pre-release snapshot). Earlier captures are logged as context only.
  **[DRAFT CHOICE — HUMAN CONFIRMS AT SIGNATURE]**

## Forward window and sample

- **Clean forward prints = scheduled NFP releases after this document is signed by a
  human.** First eligible print: the first scheduled US NFP release strictly after the
  signature date below (filled when signed). Releases before that date carry **zero v2
  verdict weight** and are not written to the v2 CSV.
- Decision point: after 8 hot-surprise trades or 14 prints, whichever comes first (~12–14 months at ~12 prints/year). One interim kill check at 7 prints.
- "Original decision date" in the missed-capture rule below = the release date of the
  14th scheduled print counted from the first eligible print.
  **[DRAFT CHOICE — HUMAN CONFIRMS AT SIGNATURE]**
- v1 and v2 rows live in separate CSVs. A print may be recorded in both, each under its
  own gate's source rules; neither gate's verdict reads the other's CSV.
  **[DRAFT CHOICE — HUMAN CONFIRMS AT SIGNATURE]**

## Per-print evidence

- Actual: BLS release-time headline number from the Employment Situation release.
- Append one row per print to **`data/macro_events/nfp_good_news_forward_v2.csv`**. Rows
  are append-only; a committed row is never edited. Each row records the MT5 forecast and
  its PIT source(s), the cross-check forecast and snapshot URL (or that it is absent), and
  the outcome (`NFP` or `MISSED_CAPTURE` with the reason).
- Wiring `scripts/nfp_forward_capture.py` to write this CSV lands as a **follow-up PR**,
  which must merge before the first eligible print. It changes collection plumbing only,
  not any rule here.
- A print whose pre-release consensus was not captured is recorded as `MISSED_CAPTURE` and excluded from the trade count; 3 or more missed captures before the decision point → the gate is decided on whatever sample exists at the original decision date (no extension).

## Pass gates (all required, evaluated once at the decision point — verbatim from v1)

- Net expectancy > 0 after 0.12% costs across forward hot-surprise trades.
- Profit factor ≥ 1.10.
- Removing the single best trade leaves net expectancy > 0.
- No parameter, window, or gate edits after sign-off.

**Pass →** escalate to an explicit human decision on a paper-live deployment, which requires its own pre-registered gate before any config or code changes.
**Fail (or interim kill: expectancy ≤ 0 with ≥ 5 hot trades at the 7-print check) →** the NFP family closes terminally; the OOS YES stands in the record as a non-replicating result. No re-opening without a named changed input.

## Scope limits

Copied verbatim from v1 (`NFP_FORWARD_GATE.md`):

- One forward CSV + the per-print collection routine. Any script Grok builds for
  this must be read-only with respect to production services.
- No optimization, no additional symbols in the verdict (ETH/SOL may be logged as
  consistency checks), no second event type, no intraday variants.

v2 additions:

- Under v2, the "one forward CSV" is `data/macro_events/nfp_good_news_forward_v2.csv`.
- `scripts/nfp_consensus_from_mt5.py` only reads the PIT log; it writes nothing and talks
  to no production service.
- No backfilling v1 rows from MT5 history.
- No threshold tuning.

## Sign-off

Parameters locked by: [PENDING HUMAN SIGNATURE - DO NOT MERGE]
Date:
