#!/usr/bin/env python3
"""Build frozen CPI/NFP surprise table for the macro-surprise drift probe (Step 0).

Consensus: Investing.com economic-calendar historical Forecast column, captured via
Wayback Machine snapshots (see data/macro_events/README_SURPRISES.md).
Actuals: BLS public API (CPI SA index CUUR0000SA0 MoM; NFP CES0000000001 level change).
"""

from __future__ import annotations

import csv
import json
import statistics
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES_CSV = ROOT / "data/macro_events/us_macro_releases.csv"
OUTPUT_CSV = ROOT / "data/macro_events/us_macro_surprises.csv"

# Investing.com Forecast column (Wayback snapshots, 2024-01 → 2026-05).
# Key: (event_type, release_date_et YYYY-MM-DD) → consensus string as published pre-release.
INVESTING_CONSENSUS: dict[tuple[str, str], str] = {
    # CPI headline MoM
    ("CPI", "2024-01-11"): "0.2%",
    ("CPI", "2024-02-13"): "0.2%",
    ("CPI", "2024-03-12"): "0.4%",
    ("CPI", "2024-04-10"): "0.3%",
    ("CPI", "2024-05-15"): "0.4%",
    ("CPI", "2024-06-12"): "0.1%",
    ("CPI", "2024-07-11"): "0.1%",
    ("CPI", "2024-08-14"): "0.2%",
    ("CPI", "2024-09-11"): "0.2%",
    ("CPI", "2024-10-10"): "0.1%",
    ("CPI", "2024-11-13"): "0.2%",
    ("CPI", "2024-12-11"): "0.3%",
    ("CPI", "2025-01-15"): "0.4%",
    ("CPI", "2025-02-12"): "0.3%",
    ("CPI", "2025-03-12"): "0.3%",
    ("CPI", "2025-04-10"): "0.1%",
    ("CPI", "2025-05-13"): "0.3%",
    ("CPI", "2025-06-11"): "0.2%",
    ("CPI", "2025-07-15"): "0.3%",
    ("CPI", "2025-08-12"): "0.2%",
    ("CPI", "2025-09-11"): "0.3%",
    ("CPI", "2025-10-24"): "0.4%",
    # 2025-12-18 Nov CPI (delayed): MoM forecast not present in Wayback captures — row omitted
    ("CPI", "2026-01-13"): "0.3%",
    ("CPI", "2026-02-13"): "0.3%",
    ("CPI", "2026-03-11"): "0.3%",
    ("CPI", "2026-04-10"): "1.0%",
    ("CPI", "2026-05-12"): "0.6%",
    # NFP headline payrolls change
    ("NFP", "2024-01-05"): "170K",
    ("NFP", "2024-02-02"): "187K",
    ("NFP", "2024-03-08"): "198K",
    ("NFP", "2024-04-05"): "212K",
    ("NFP", "2024-05-03"): "238K",
    ("NFP", "2024-06-07"): "182K",
    ("NFP", "2024-07-05"): "191K",
    ("NFP", "2024-08-02"): "176K",
    ("NFP", "2024-09-06"): "164K",
    ("NFP", "2024-10-04"): "147K",
    ("NFP", "2024-11-01"): "106K",
    ("NFP", "2024-12-06"): "202K",
    ("NFP", "2025-01-10"): "164K",
    ("NFP", "2025-02-07"): "169K",
    ("NFP", "2025-03-07"): "159K",
    ("NFP", "2025-04-04"): "137K",
    ("NFP", "2025-05-02"): "138K",
    ("NFP", "2025-06-06"): "126K",
    ("NFP", "2025-07-03"): "111K",
    ("NFP", "2025-08-01"): "106K",
    ("NFP", "2025-09-05"): "75K",
    ("NFP", "2025-11-20"): "53K",
    (
        "NFP",
        "2025-12-16",
    ): "51K",  # Nov payrolls; Oct same-day release had no published forecast in archive
    ("NFP", "2026-01-09"): "66K",
    ("NFP", "2026-02-11"): "66K",
    ("NFP", "2026-03-06"): "58K",
    ("NFP", "2026-04-03"): "65K",
    ("NFP", "2026-05-08"): "65K",
}

MIN_COVERAGE_EVENTS = 17  # strict majority of ~28 per series (56 total CPI+NFP)
MAX_MISSING_TOTAL = 39  # block if >=40 of 56 lack consensus


@dataclass(frozen=True)
class SurpriseRow:
    event_type: str
    release_date_et: str
    release_ts_utc: str
    metric: str
    actual: float
    consensus: float
    surprise: float
    z: float
    consensus_source: str
    actual_source: str
    consensus_note: str


def _parse_pct(raw: str) -> float:
    text = raw.strip().replace("%", "")
    return float(text)


def _parse_nfp_k(raw: str) -> float:
    text = raw.strip().upper().replace("K", "").replace(",", "")
    return float(text)


def _parse_release_ts(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _fetch_bls_series() -> tuple[dict[str, float], dict[str, float]]:
    payload = json.dumps(
        {"seriesid": ["CUUR0000SA0", "CES0000000001"], "startyear": "2023", "endyear": "2026"}
    ).encode()
    req = urllib.request.Request(
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API failed: {data}")

    cpi: dict[str, float] = {}
    nfp: dict[str, float] = {}
    for series in data["Results"]["series"]:
        sid = series["seriesID"]
        for point in series["data"]:
            if point["value"] in ("-", ""):
                continue
            key = f"{point['year']}-{point['period'][1:]}"
            val = float(point["value"])
            if sid == "CUUR0000SA0":
                cpi[key] = val
            else:
                nfp[key] = val
    return cpi, nfp


def _reference_month(release_date_et: str) -> str:
    """Return YYYY-MM for the data month (release in month M reports M-1)."""
    year, month, _ = (int(x) for x in release_date_et.split("-"))
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def _cpi_mom_actual(cpi: dict[str, float], release_date_et: str) -> float | None:
    ref = _reference_month(release_date_et)
    year, month = (int(x) for x in ref.split("-"))
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    cur_key = f"{year}-{month:02d}"
    prev_key = f"{prev_year}-{prev_month:02d}"
    if cur_key not in cpi or prev_key not in cpi:
        return None
    return (cpi[cur_key] / cpi[prev_key] - 1.0) * 100.0


def _nfp_change_actual(nfp: dict[str, float], release_date_et: str) -> float | None:
    ref = _reference_month(release_date_et)
    year, month = (int(x) for x in ref.split("-"))
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    cur_key = f"{year}-{month:02d}"
    prev_key = f"{prev_year}-{prev_month:02d}"
    if cur_key not in nfp or prev_key not in nfp:
        return None
    return nfp[cur_key] - nfp[prev_key]


def _load_release_rows() -> list[dict[str, str]]:
    with RELEASES_CSV.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row["event_type"] in ("CPI", "NFP")]


def build_surprises() -> tuple[list[SurpriseRow], list[str]]:
    cpi_levels, nfp_levels = _fetch_bls_series()
    releases = _load_release_rows()
    warnings: list[str] = []
    draft: list[tuple[SurpriseRow, bool]] = []

    for row in releases:
        event_type = row["event_type"]
        release_date = row["release_date_et"]
        consensus_raw = INVESTING_CONSENSUS.get((event_type, release_date))
        has_consensus = consensus_raw is not None
        if not has_consensus:
            warnings.append(f"missing consensus: {event_type} {release_date}")
            continue

        if event_type == "CPI":
            actual = _cpi_mom_actual(cpi_levels, release_date)
            if actual is None:
                warnings.append(f"missing BLS CPI actual: {release_date}")
                continue
            consensus = _parse_pct(consensus_raw)
            metric = "headline_cpi_mom_pct"
            note = ""
        else:
            actual = _nfp_change_actual(nfp_levels, release_date)
            if actual is None:
                warnings.append(f"missing BLS NFP actual: {release_date}")
                continue
            consensus = _parse_nfp_k(consensus_raw)
            metric = "headline_nfp_change_k"
            note = ""
            if release_date == "2025-12-16":
                note = "Nov NFP on delayed Dec-16 slot; Oct same-day had no forecast in archive"

        draft.append(
            (
                SurpriseRow(
                    event_type=event_type,
                    release_date_et=release_date,
                    release_ts_utc=row["release_ts_utc"],
                    metric=metric,
                    actual=round(actual, 4),
                    consensus=round(consensus, 4),
                    surprise=0.0,
                    z=0.0,
                    consensus_source="investing.com/Wayback",
                    actual_source="bls.gov",
                    consensus_note=note,
                ),
                has_consensus,
            )
        )

    missing = len(releases) - len(draft)
    if missing >= MAX_MISSING_TOTAL:
        raise RuntimeError(
            f"BLOCKED_ON_DATA: {missing}/{len(releases)} events lack consensus+actual"
        )

    by_type: dict[str, list[float]] = {}
    for item, _ in draft:
        by_type.setdefault(item.event_type, []).append(item.actual - item.consensus)

    finalized: list[SurpriseRow] = []
    for item, _ in draft:
        stdev = statistics.pstdev(by_type[item.event_type])
        z = (item.actual - item.consensus) / stdev if stdev > 0 else 0.0
        finalized.append(
            SurpriseRow(
                event_type=item.event_type,
                release_date_et=item.release_date_et,
                release_ts_utc=item.release_ts_utc,
                metric=item.metric,
                actual=item.actual,
                consensus=item.consensus,
                surprise=round(item.actual - item.consensus, 4),
                z=round(z, 4),
                consensus_source=item.consensus_source,
                actual_source=item.actual_source,
                consensus_note=item.consensus_note,
            )
        )

    for event_type in ("CPI", "NFP"):
        count = sum(1 for row in finalized if row.event_type == event_type)
        if count < MIN_COVERAGE_EVENTS:
            warnings.append(
                f"thin coverage: {event_type} only {count} events with consensus+actual"
            )

    return finalized, warnings


def write_csv(rows: Sequence[SurpriseRow]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "event_type",
        "release_date_et",
        "release_ts_utc",
        "metric",
        "actual",
        "consensus",
        "surprise",
        "z",
        "consensus_source",
        "actual_source",
        "consensus_note",
    ]
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item.release_ts_utc):
            writer.writerow(
                {
                    "event_type": row.event_type,
                    "release_date_et": row.release_date_et,
                    "release_ts_utc": row.release_ts_utc,
                    "metric": row.metric,
                    "actual": row.actual,
                    "consensus": row.consensus,
                    "surprise": row.surprise,
                    "z": row.z,
                    "consensus_source": row.consensus_source,
                    "actual_source": row.actual_source,
                    "consensus_note": row.consensus_note,
                }
            )


def main() -> int:
    rows, warnings = build_surprises()
    write_csv(rows)
    print(f"wrote {len(rows)} rows to {OUTPUT_CSV}")
    for warning in warnings:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
