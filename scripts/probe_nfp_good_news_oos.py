#!/usr/bin/env python3
"""Run the pre-registered NFP good-news-is-good BTCUSDT OOS probe.

The probe is deliberately file-based: both the point-in-time surprise table and
the 1h OHLCV input are frozen artifacts. It never queries production services.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_macro_event_drift import HourlyBar, entry_bar_index

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
YES = "YES"
NO_PULSE = "NO_PULSE"
MIN_RECOVERED_EVENTS = 19
ROUND_TRIP_COST_PCT = 0.12
MAX_DRAWDOWN_PCT = 10.0
OOS_START = datetime(2021, 1, 8, tzinfo=UTC)
OOS_END = datetime(2023, 12, 8, 23, 59, 59, tzinfo=UTC)
DEFAULT_SURPRISES_CSV = Path("data/macro_events/nfp_good_news_oos_2021_2023.csv")
DEFAULT_OHLCV_CSV = Path("data/macro_events/BTCUSDT_1h_2021-01-01_2024-01-01.csv")


@dataclass(frozen=True)
class NfpSurprise:
    release_date_et: str
    release_ts: datetime
    actual: float
    consensus: float
    surprise: float
    z: float
    consensus_source: str
    actual_source: str
    source_snapshot_url: str


@dataclass(frozen=True)
class Trade:
    release_ts: str
    z: float
    entry_ts: str
    exit_ts: str
    gross_return_pct: float
    net_return_pct: float


@dataclass(frozen=True)
class ProbeMetrics:
    trade_count: int
    net_expectancy_pct: float
    profit_factor: float | None
    max_drawdown_pct: float
    leave_one_out_min_expectancy_pct: float | None
    all_leave_one_out_positive: bool


@dataclass(frozen=True)
class ProbeReport:
    status: str
    verdict: str
    reasons: tuple[str, ...]
    surprise_rows: int
    aligned_events: int
    excluded_events: tuple[str, ...]
    input_sha256: dict[str, str]
    trades: tuple[Trade, ...]
    metrics: ProbeMetrics | None


def _parse_utc(raw: str) -> datetime:
    value = raw.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_surprises(path: Path) -> tuple[NfpSurprise, ...]:
    required = {
        "event_type",
        "release_date_et",
        "release_ts_utc",
        "actual",
        "consensus",
        "surprise",
        "z",
        "consensus_source",
        "actual_source",
        "source_snapshot_url",
    }
    rows: list[NfpSurprise] = []
    seen: set[datetime] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"NFP surprise CSV is missing required columns: {path}")
        for line_number, row in enumerate(reader, start=2):
            if row["event_type"].strip() != "NFP":
                raise ValueError(f"row {line_number}: event_type must be NFP")
            release_ts = _parse_utc(row["release_ts_utc"])
            if not OOS_START <= release_ts <= OOS_END:
                raise ValueError(f"row {line_number}: release timestamp outside locked OOS window")
            if release_ts in seen:
                raise ValueError(
                    f"row {line_number}: duplicate release timestamp {release_ts.isoformat()}"
                )
            seen.add(release_ts)
            source_snapshot_url = row["source_snapshot_url"].strip()
            if not source_snapshot_url.startswith("https://web.archive.org/"):
                raise ValueError(f"row {line_number}: source_snapshot_url must be a Wayback URL")
            actual = float(row["actual"])
            consensus = float(row["consensus"])
            surprise = float(row["surprise"])
            if not math.isclose(surprise, actual - consensus, abs_tol=1e-9):
                raise ValueError(f"row {line_number}: surprise must equal actual minus consensus")
            rows.append(
                NfpSurprise(
                    release_date_et=row["release_date_et"].strip(),
                    release_ts=release_ts,
                    actual=actual,
                    consensus=consensus,
                    surprise=surprise,
                    z=float(row["z"]),
                    consensus_source=row["consensus_source"].strip(),
                    actual_source=row["actual_source"].strip(),
                    source_snapshot_url=source_snapshot_url,
                )
            )
    if len(rows) < 2:
        raise ValueError("NFP surprise CSV needs at least two rows to standardize surprises")
    mean = sum(item.surprise for item in rows) / len(rows)
    stdev = (sum((item.surprise - mean) ** 2 for item in rows) / len(rows)) ** 0.5
    if stdev <= 0:
        raise ValueError("NFP surprise CSV has zero surprise standard deviation")
    for item in rows:
        if not math.isclose(item.z, item.surprise / stdev, rel_tol=1e-3, abs_tol=1e-6):
            raise ValueError("NFP surprise CSV z values do not match the committed OOS sample")
    return tuple(sorted(rows, key=lambda item: item.release_ts))


def load_hourly_bars(path: Path) -> tuple[HourlyBar, ...]:
    bars: list[HourlyBar] = []
    seen: set[datetime] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {"time", "close_price"}.issubset(reader.fieldnames):
            raise ValueError(f"OHLCV CSV is missing time/close_price columns: {path}")
        for line_number, row in enumerate(reader, start=2):
            timestamp = _parse_utc(row["time"])
            close = float(row["close_price"])
            if timestamp in seen:
                raise ValueError(
                    f"row {line_number}: duplicate OHLCV timestamp {timestamp.isoformat()}"
                )
            if close <= 0:
                raise ValueError(f"row {line_number}: close_price must be positive")
            seen.add(timestamp)
            bars.append(HourlyBar(time=timestamp, close_price=close))
    ordered = tuple(sorted(bars, key=lambda item: item.time))
    if tuple(item.time for item in bars) != tuple(item.time for item in ordered):
        raise ValueError("OHLCV CSV must be time ordered")
    return ordered


def _max_drawdown_pct(returns_pct: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns_pct:
        equity *= 1.0 + value / 100.0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return max_drawdown * 100.0


def _profit_factor(returns_pct: Sequence[float]) -> float | None:
    gains = sum(value for value in returns_pct if value > 0)
    losses = -sum(value for value in returns_pct if value < 0)
    if losses == 0:
        return None if gains == 0 else math.inf
    return gains / losses


def compute_metrics(trades: Sequence[Trade]) -> ProbeMetrics:
    returns = [trade.net_return_pct for trade in trades]
    expectancy = sum(returns) / len(returns) if returns else 0.0
    leave_one_out = [
        sum(returns[:index] + returns[index + 1 :]) / (len(returns) - 1)
        for index in range(len(returns))
        if len(returns) > 1
    ]
    return ProbeMetrics(
        trade_count=len(trades),
        net_expectancy_pct=expectancy,
        profit_factor=_profit_factor(returns),
        max_drawdown_pct=_max_drawdown_pct(returns),
        leave_one_out_min_expectancy_pct=min(leave_one_out) if leave_one_out else None,
        all_leave_one_out_positive=bool(leave_one_out)
        and all(value > 0 for value in leave_one_out),
    )


def _build_trades(
    surprises: Sequence[NfpSurprise], bars: Sequence[HourlyBar]
) -> tuple[tuple[Trade, ...], tuple[str, ...], int]:
    trades: list[Trade] = []
    excluded: list[str] = []
    aligned_events = 0
    for surprise in surprises:
        entry_index = entry_bar_index(bars, surprise.release_ts)
        if entry_index is None or entry_index + 24 >= len(bars):
            excluded.append(f"{surprise.release_ts.isoformat()}: insufficient OHLCV")
            continue
        aligned_events += 1
        if surprise.z <= 0:
            continue
        entry = bars[entry_index]
        exit_bar = bars[entry_index + 24]
        gross_return = (exit_bar.close_price / entry.close_price - 1.0) * 100.0
        trades.append(
            Trade(
                release_ts=surprise.release_ts.isoformat(),
                z=surprise.z,
                entry_ts=entry.time.isoformat(),
                exit_ts=exit_bar.time.isoformat(),
                gross_return_pct=gross_return,
                net_return_pct=gross_return - ROUND_TRIP_COST_PCT,
            )
        )
    return tuple(trades), tuple(excluded), aligned_events


def decide_verdict(
    *,
    aligned_events: int,
    metrics: ProbeMetrics | None,
) -> tuple[str, str, tuple[str, ...]]:
    if aligned_events < MIN_RECOVERED_EVENTS:
        return (
            BLOCKED_ON_DATA,
            BLOCKED_ON_DATA,
            (f"only {aligned_events} data-aligned events; need >= {MIN_RECOVERED_EVENTS}",),
        )
    if metrics is None or metrics.trade_count < 2:
        return ("OK", NO_PULSE, ("fewer than two hot-surprise trades after locked filtering",))

    failures: list[str] = []
    if metrics.net_expectancy_pct <= 0:
        failures.append("net expectancy <= 0 after 0.12% round-trip cost")
    if metrics.profit_factor is None or metrics.profit_factor < 1.10:
        failures.append("profit factor < 1.10")
    if metrics.max_drawdown_pct > MAX_DRAWDOWN_PCT:
        failures.append(f"max drawdown > {MAX_DRAWDOWN_PCT:.0f}%")
    if not metrics.all_leave_one_out_positive:
        failures.append("result depends on at least one trade")
    if failures:
        return ("OK", NO_PULSE, tuple(failures))
    return ("OK", YES, ("all pre-registered NFP OOS gates passed",))


def _input_hashes(surprises_csv: Path, ohlcv_csv: Path) -> dict[str, str]:
    return {
        str(path): _sha256(path) if path.is_file() else "missing"
        for path in (surprises_csv, ohlcv_csv)
    }


def run_probe(surprises_csv: Path, ohlcv_csv: Path) -> ProbeReport:
    missing = [str(path) for path in (surprises_csv, ohlcv_csv) if not path.is_file()]
    if missing:
        return ProbeReport(
            status=BLOCKED_ON_DATA,
            verdict=BLOCKED_ON_DATA,
            reasons=(f"missing immutable input: {', '.join(missing)}",),
            surprise_rows=0,
            aligned_events=0,
            excluded_events=(),
            input_sha256=_input_hashes(surprises_csv, ohlcv_csv),
            trades=(),
            metrics=None,
        )
    surprises = load_surprises(surprises_csv)
    bars = load_hourly_bars(ohlcv_csv)
    trades, excluded, aligned_events = _build_trades(surprises, bars)
    metrics = compute_metrics(trades) if aligned_events >= MIN_RECOVERED_EVENTS else None
    status, verdict, reasons = decide_verdict(aligned_events=aligned_events, metrics=metrics)
    return ProbeReport(
        status=status,
        verdict=verdict,
        reasons=reasons,
        surprise_rows=len(surprises),
        aligned_events=aligned_events,
        excluded_events=excluded,
        input_sha256=_input_hashes(surprises_csv, ohlcv_csv),
        trades=trades,
        metrics=metrics,
    )


def report_to_json(report: ProbeReport) -> dict[str, object]:
    payload = asdict(report)
    metrics = payload.get("metrics")
    if isinstance(metrics, dict) and math.isinf(metrics.get("profit_factor") or 0):
        metrics["profit_factor"] = "infinity"
    return payload


def render_report(report: ProbeReport) -> str:
    lines = [
        "# NFP Good-News-Is-Good OOS Probe",
        "",
        f"**Status:** **{report.status}**",
        f"**Verdict:** **{report.verdict}**",
        "",
        "## Inputs",
        f"- Surprise rows: {report.surprise_rows}",
        f"- Data-aligned events: {report.aligned_events}",
    ]
    for path, digest in report.input_sha256.items():
        lines.append(f"- `{path}` SHA-256: `{digest}`")
    lines.extend(["", "## Decision"])
    lines.extend(f"- {reason}" for reason in report.reasons)
    if report.metrics is not None:
        metrics = report.metrics
        pf = "infinity" if metrics.profit_factor == math.inf else str(metrics.profit_factor)
        lines.extend(
            [
                "",
                "## Metrics",
                f"- Hot-surprise trades: {metrics.trade_count}",
                f"- Net expectancy: {metrics.net_expectancy_pct:.4f}%",
                f"- Profit factor: {pf}",
                f"- Max drawdown: {metrics.max_drawdown_pct:.4f}%",
                "- Leave-one-out minimum expectancy: "
                + (
                    f"{metrics.leave_one_out_min_expectancy_pct:.4f}%"
                    if metrics.leave_one_out_min_expectancy_pct is not None
                    else "not available"
                ),
                f"- All leave-one-out expectancies positive: {metrics.all_leave_one_out_positive}",
            ]
        )
    if report.excluded_events:
        lines.extend(["", "## Excluded Events"])
        lines.extend(f"- {item}" for item in report.excluded_events)
    lines.extend(
        [
            "",
            "## Trades",
            "",
            "| Release | z | Entry | Exit | Gross % | Net % |",
            "|---|---:|---|---|---:|---:|",
        ]
    )
    for trade in report.trades:
        lines.append(
            "| "
            f"{trade.release_ts} | {trade.z:.4f} | {trade.entry_ts} | {trade.exit_ts} | "
            f"{trade.gross_return_pct:.4f} | {trade.net_return_pct:.4f} |"
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pre-registered NFP good-news-is-good OOS probe")
    parser.add_argument("--surprises-csv", type=Path, default=DEFAULT_SURPRISES_CSV)
    parser.add_argument("--ohlcv-csv", type=Path, default=DEFAULT_OHLCV_CSV)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-report", type=Path)
    parser.add_argument(
        "--json", action="store_true", help="Write JSON to stdout instead of Markdown"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_probe(args.surprises_csv, args.ohlcv_csv)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report_to_json(report), indent=2) + "\n", encoding="utf-8"
        )
    if args.output_report:
        args.output_report.parent.mkdir(parents=True, exist_ok=True)
        args.output_report.write_text(render_report(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report_to_json(report), indent=2))
    else:
        print(render_report(report), end="")
    if report.verdict == BLOCKED_ON_DATA:
        return 2
    return 0 if report.verdict == YES else 1


if __name__ == "__main__":
    raise SystemExit(main())
