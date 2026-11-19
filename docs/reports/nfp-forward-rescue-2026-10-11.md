# NFP forward rescue — Oct 2 miss, Nov 6 capture (2026-11-17)

**Gate:** `docs/evidence_portfolio/NFP_FORWARD_GATE.md` (signed 2026-07-21)
**Action:** append-only evidence for the two missing prints. No rule, threshold, or
gate document was edited. No production service, config, or order path was touched.

## Result

| Release | Outcome | Consensus | Actual | Surprise | z |
|---------|---------|-----------|--------|----------|---|
| 2026-10-02 | `MISSED_CAPTURE` | — | — | — | — |
| 2026-11-06 | `NFP` (hot) | 65.0K | 165.0K | +100.0K | 0.4539676775 |

The November print is a **hot surprise**, so the signed rule maps it to a long entry
at the close of the first 1h BTCUSDT candle at or after `2026-11-06T13:30:00Z`, exit
at the first close at or after entry + 24h. This report records the measurement row;
it does not execute or report a trade.

Missed captures are now **3** (2026-08-07, 2026-09-04, 2026-10-02). Per the signed
gate, 3+ misses cap the sample at the original decision date; there is no extension.
The gate is not decided or modified here.

## October 2 — no pre-release capture

Wayback CDX over the gate's 24h window
`[2026-10-01T12:30:00Z, 2026-10-02T12:30:00Z)` returned no rows:

```text
$ curl -sS "https://web.archive.org/cdx/search/cdx?url=www.investing.com/economic-calendar/nonfarm-payrolls-227&from=20261001123000&to=20261002123000&output=json&fl=timestamp,statuscode,original"
[]
```

A wider September–early-October sweep shows only a `403` on 2026-09-16 and a `200`
on **2026-10-04 12:16:48**, after the print:

```text
[["timestamp","statuscode"],
["20260916141246","403"],
["20261004121648","200"]]
```

The post-release page cannot supply a pre-release consensus, so the row is recorded
as `MISSED_CAPTURE` with the CDX evidence in its note.

## November 6 — two valid pre-release snapshots

CDX over `[2026-11-05T13:30:00Z, 2026-11-06T13:30:00Z)` returned two `200` captures:

```text
[["timestamp","statuscode","original"],
["20261106070735","200","https://www.investing.com/economic-calendar/nonfarm-payrolls-227"],
["20261106130744","200","https://www.investing.com/economic-calendar/nonfarm-payrolls-227"]]
```

The later capture (22 minutes before the 13:30 UTC release) was used:

<https://web.archive.org/web/20261106130744/https://www.investing.com/economic-calendar/nonfarm-payrolls-227>

At capture time the page's `Latest Release` was **Oct 02, 2026** (Actual 29.00K,
Forecast 89.00K, Previous 133.00K), and the scheduled row was:

```text
Release date            Time    Actual   Forecast   Previous
Nov 06, 2026 (Oct)      13:30            65.00K     29.00K
```

Forecast **65.00K** is therefore the pre-release consensus. The 07:07 capture shows
the same forecast; it is a second confirmation.

## Actual

BLS Employment Situation, October 2026 (USDL-26-1632), released 2026-11-06 08:30 ET:

```text
Total nonfarm payroll employment rose by 165,000 in October ...
```

Source: <https://www.bls.gov/news.release/empsit.nr0.htm> (archived release:
<https://www.bls.gov/news.release/archives/empsit_11062026.htm>).

Actual **165.0K**; surprise `165.0 − 65.0 = +100.0K`; `z = 100.0 / 220.28 =
0.4539676775`.

## Tooling

Two capture-tool fixes made the rescue possible without changing the gate:

1. `verify_snapshot_is_point_in_time` now reads the page's `Latest Release` label.
   A pre-release page legitimately lists the upcoming release as a scheduled row
   with a forecast; the old "must not mention the upcoming date" check rejected it.
   The label must be the previous print (or the legacy containment check applies).
2. `pre --rescue` accepts an already-passed release when `--snapshot-url` is given,
   and enforces the 24h window against the **snapshot timestamp** rather than the
   current time. `post` then appends normally.

Tests: `uv run pytest tests/test_nfp_forward_capture.py -q` (27 passed).

## Appended rows

`data/macro_events/nfp_good_news_forward.csv`:

```text
NFP_MISSED_CAPTURE,2026-10-02,...,No pre-release Investing.com/Wayback snapshot in the 24h window; CDX shows a 403 on 2026-09-16 and only a post-release 200 on 2026-10-04,
NFP,2026-11-06,2026-11-06T13:30:00Z,headline_nfp_change_k,165.0,65.0,100.0,0.4539676775013619,investing.com/Wayback,bls.gov,https://web.archive.org/web/20261106130744/https://www.investing.com/economic-calendar/nonfarm-payrolls-227
```

Rows are append-only; the two existing August/September rows are unchanged.
