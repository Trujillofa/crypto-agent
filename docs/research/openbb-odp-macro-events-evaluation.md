# Evaluation: OpenBB Open Data Platform (ODP) for macro events — REJECT

**Date:** 2026-10-10
**Verdict:** Reject. Do not adopt.
**Scope evaluated:** using `openbb` (ODP) to populate
`src/strategy/macro_volatility.py::MacroEvent` and the macro-event drift probe.

## Summary

The Open Data Platform was evaluated as a macro-event data source for
`MacroEventFeed`. It was rejected for two independent reasons:

1. It **cannot populate the field the feed exists to compute.**
   `MacroEvent.surprise_pct` is the reason `MacroEventFeed` gates trading at
   all, and no keyless ODP endpoint exposes the consensus and actual values
   needed to derive it.
2. It **duplicates tooling this repo already has**, with worse provenance.
   See [Duplication](#duplication-of-existing-tooling).

No runtime, config, or dependency change is proposed or made by this document.

## Measured behaviour

Environment: Python 3.13 venv, `pip install openbb` (base) plus
`pip install openbb-economy`. No API keys configured.

### What the base package actually ships

A base `openbb` install exposes only alternative-data routers:

```
bls cboe cftc coverage deribit ecb eia famafrench federal_reserve finra fred
imf jodi nasdaq news oecd reference sec system tmx uscongress usda ustreasury
```

The conventional routers are **absent** and require separate extension packages:

| Router | Base install |
|--------|--------------|
| `economy` | missing — needs `openbb-economy` |
| `equity` | missing |
| `crypto` | missing |
| `fx` | missing |
| `index` | missing |
| `technical` | missing |
| `quant` | missing |

### Field mapping against `MacroEvent`

`src/strategy/macro_volatility.py` defines:

```python
@dataclass
class MacroEvent:
    name: str
    timestamp: float
    expected_value: float | None = None
    actual_value: float | None = None
    surprise_pct: float = 0.0  # (actual - expected) / expected * 100
    direction: str = ""
```

| Field | `obb.federal_reserve.release_calendar` | `obb.economy.calendar` |
|-------|----------------------------------------|------------------------|
| `name` | `title` | `event` |
| `timestamp` | `date` + `time` | dataframe index |
| `expected_value` | absent | absent |
| `actual_value` | absent | absent |
| `surprise_pct` | **not derivable** | **not derivable** |

Observed schemas:

```
federal_reserve.release_calendar -> date | time | release | title | event_type   (161 rows)
economy.calendar                 -> country | category | event | source | reference_period (43 rows)
```

Both are **release schedules**, not surprise feeds. Consensus and actual values
sit behind a paid provider. This is a hard blocker: without them the surprise
gate cannot function, and a schedule-only feed would silently supply
`surprise_pct = 0.0`, which reads as "no surprise" rather than "unknown".

## Duplication of existing tooling

This repo already captures both halves of the macro-event problem, with
provenance that ODP does not provide.

### Surprise capture already exists

`data/macro_events/nfp_good_news_forward.csv` is an append-only log with the
exact fields `MacroEvent` needs:

```
event_type,release_date_et,release_ts_utc,metric,actual,consensus,surprise,z,
consensus_source,actual_source,source_snapshot_url
```

It is produced by `scripts/nfp_forward_capture.py` under the signed gate
`docs/evidence_portfolio/NFP_FORWARD_GATE.md`, using a point-in-time Wayback
snapshot of the consensus taken *before* the release — a stronger provenance
guarantee than any aggregator API, since the pre-release consensus can be
audited after the fact. Missed captures are recorded as `NFP_MISSED_CAPTURE`
and capped, so sample integrity is self-policing.

### Release timing already exists

`data/macro_events/us_macro_releases.csv` holds 75 hand-verified ex-ante
release timestamps (19 FOMC, 28 CPI, 28 NFP) from bls.gov and
federalreserve.gov, per `data/macro_events/README.md`. It is consumed by
`scripts/probe_macro_event_drift.py`.

ODP's Fed calendar is the **same public source** (federalreserve.gov), but
unfiltered: 161 rows where `event_type` is `Stat` and the mix is dominated by
routine series such as `CP - Commercial Paper` and `H.15 - Selected Interest
Rates`. It is the same data, less curated.

### Why this is a negative outcome

`CLAUDE.md` is explicit that redundant layers should be removed, not added:

> **Redundant layers**: If a wrapper adds no logic, remove it. If an
> abstraction has one implementation, inline it.

An ODP-backed path would sit alongside `nfp_forward_capture.py` and
`us_macro_releases.csv`, supply strictly less, and add a ~200-package
dependency tree to a runtime whose safety rules are fail-closed.

## Rejected alternatives

| Option | Verdict | Reason |
|--------|---------|--------|
| ODP as `MacroEvent` provider | reject | No consensus/actual; surprise gate inoperable |
| ODP as release-timing source | reject | Duplicate of `us_macro_releases.csv`, worse curation |
| ODP for FRED macro series | not needed | FRED is directly reachable; series data is not a gap here |
| ODP for equity/crypto research | out of scope | This repo trades Binance perps via direct ingest |

## Revisit condition

Re-open this evaluation only if **all** of the following hold:

- A keyless or already-budgeted provider exposes consensus and actual values
  with retrievable provenance.
- `nfp_good_news_forward.csv` is retired in favour of it, not merely joined by it.
- `MacroEvent.surprise_pct` can be populated from that source at the
  point-in-time the gate requires.

Absent the first condition, no ODP endpoint can satisfy `MacroEvent`.

## References

- `src/strategy/macro_volatility.py`
- `scripts/nfp_forward_capture.py`
- `scripts/probe_macro_event_drift.py`
- `data/macro_events/README.md`
- `data/macro_events/us_macro_releases.csv`
- `data/macro_events/nfp_good_news_forward.csv`
- `docs/evidence_portfolio/NFP_FORWARD_GATE.md`
- ODP repo: <https://github.com/openbq-org/OpenBB> (Apache-2.0, `pip install openbb`)
