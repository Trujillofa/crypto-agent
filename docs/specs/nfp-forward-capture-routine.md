# NFP Forward-Gate Capture Routine — Build Brief (for Grok)

**Parent gate:** `docs/evidence_portfolio/NFP_FORWARD_GATE.md` (signed 2026-07-21, in force)
**First deadline:** capture window for the 2026-08-07 release opens 2026-08-06 12:30 UTC
**Scope class:** read-only tooling; no production services touched; no optimization

## What the gate requires per print

1. **Before the release** (within 24h before 08:30 ET on release day): trigger a
   Wayback "Save Page Now" of
   `https://www.investing.com/economic-calendar/nonfarm-payrolls-227` and record the
   returned snapshot URL. This freezes the consensus point-in-time.
2. **After the release**: read the headline actual from the BLS Employment Situation
   release (`bls.gov/news.release/empsit.nr0.htm`, archived copy under
   `bls.gov/news.release/archives/`).
3. Append **one row** to `data/macro_events/nfp_good_news_forward.csv` — same columns
   as `data/macro_events/nfp_good_news_oos_2021_2023.csv`:
   `event_type,release_date_et,release_ts_utc,metric,actual,consensus,surprise,z,consensus_source,actual_source,source_snapshot_url`
   - `surprise = actual − consensus`
   - `z = surprise / 220.28` (divisor frozen by the gate; reporting continuity only)
   - `release_ts_utc` = 08:30 America/New_York converted to UTC, DST-aware
4. Rows are **append-only**. A print with no pre-release capture gets a
   `MISSED_CAPTURE` row (see gate for semantics; 3 misses caps the sample).

## Suggested implementation (small)

- `scripts/nfp_forward_capture.py` with two subcommands:
  - `pre` — POST/GET `https://web.archive.org/save/https://www.investing.com/economic-calendar/nonfarm-payrolls-227`,
    verify the snapshot resolves and its "Latest Release" date is the *previous*
    release (i.e., the page predates the upcoming print), print the snapshot URL, and
    stash it in `research/nfp_forward/pending_capture.json`.
  - `post --actual <n> --consensus <n>` — validate against the pending snapshot,
    compute surprise/z (full float precision — the OOS loader rejects rounded z),
    append the CSV row, clear the pending file.
- Reuse the validation logic pattern from
  `scripts/probe_nfp_good_news_oos.py::load_surprises` for self-checks.
- No cron/systemd automation of the *decision*; scheduling the `pre` call before each
  release day is fine (first Fridays; human reminder exists for 2026-08-06).

## Explicitly out of scope (gate terms)

- No trading, paper agent, config, or strategy code.
- No extra symbols in the verdict path, no second event type, no intraday variants.
- No edits to committed CSV rows, ever.

## Upcoming release dates (first Fridays, 08:30 ET)

2026-08-07, 2026-09-04, 2026-10-02, 2026-11-06, 2026-12-04 — verify each against the
BLS release schedule (`bls.gov/schedule/news_release/empsit.htm`) the week before;
BLS occasionally shifts a week.
