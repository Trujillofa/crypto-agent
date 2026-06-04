#!/usr/bin/env python3
"""Compare entry-time overlap across agent configs on the same symbol/timeframe."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.experiment_autopilot import (  # noqa: E402
    _build_backtest_config,
    _db_config_from_settings,
    _resolve_data_range,
    _run_backtest,
)
from scripts.run_autoresearch import _deep_merge, _read_yaml  # noqa: E402
from src.backtest.experiment_autopilot import build_wfo_windows  # noqa: E402
from src.db import close_pool, get_pool, init_pool  # noqa: E402
from src.features.reader import IndicatorReader  # noqa: E402
from src.main import _resolve_strategy_config, load_settings  # noqa: E402
from src.utils.logger import configure_logger  # noqa: E402


@dataclass(frozen=True)
class AgentSpec:
    label: str
    config_path: Path
    base_config_path: Path | None
    replay_sentiment_log: str | None
    replay_sentiment_max_age_hours: float | None
    note: str | None


@dataclass(frozen=True)
class EntrySet:
    label: str
    symbol: str
    timeframe: str
    entries: list[datetime]
    note: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze entry-time overlap across agents")
    parser.add_argument(
        "--manifest",
        default="config/autoresearch/overlap_manifest_sol_1h.yaml",
        help="YAML manifest listing agents to compare",
    )
    parser.add_argument("--symbol", help="Override manifest symbol")
    parser.add_argument("--timeframe", help="Override manifest timeframe")
    parser.add_argument("--train-months", type=int, help="WFO train months")
    parser.add_argument("--test-months", type=int, help="WFO test months")
    parser.add_argument(
        "--tolerance-hours",
        type=int,
        default=1,
        help="Match entries within this many hours as overlapping",
    )
    parser.add_argument(
        "--output",
        default="docs/reports/entry-overlap-sol-1h.json",
        help="JSON report output path",
    )
    parser.add_argument(
        "--include-live-db",
        action="store_true",
        help="Append live positions overlap from TimescaleDB (last 730 days)",
    )
    parser.add_argument("--live-days", type=int, default=730)
    parser.add_argument(
        "--agent-ids",
        nargs="*",
        default=[
            "sol-1h-trend-pullback-overlay-live",
            "sentiment-macro-bot",
        ],
        help="agent_id values for live DB overlap section",
    )
    return parser.parse_args()


def _load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def _parse_agents(manifest: dict[str, Any]) -> list[AgentSpec]:
    agents_raw = manifest.get("agents")
    if not isinstance(agents_raw, list):
        raise ValueError("manifest.agents must be a list")

    specs: list[AgentSpec] = []
    for item in agents_raw:
        if not isinstance(item, dict):
            continue
        label = str(item["label"])
        config_path = Path(str(item["config"]))
        base = item.get("base_config")
        specs.append(
            AgentSpec(
                label=label,
                config_path=config_path,
                base_config_path=Path(str(base)) if base else None,
                replay_sentiment_log=(
                    str(item["replay_sentiment_log"]) if item.get("replay_sentiment_log") else None
                ),
                replay_sentiment_max_age_hours=(
                    float(item["replay_sentiment_max_age_hours"])
                    if item.get("replay_sentiment_max_age_hours") is not None
                    else None
                ),
                note=str(item["note"]) if item.get("note") else None,
            )
        )
    return specs


def _resolve_config_path(spec: AgentSpec) -> Path:
    if spec.base_config_path is None:
        return spec.config_path
    merged = _deep_merge(_read_yaml(spec.base_config_path), _read_yaml(spec.config_path))
    out = Path("research/overlap-resolved") / f"{spec.label}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    return out


def _normalize_entry(ts: str, *, timeframe: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if timeframe.endswith("h"):
        hours = int(timeframe[:-1])
        floored = dt.replace(minute=0, second=0, microsecond=0)
        hour_bucket = (floored.hour // hours) * hours
        return floored.replace(hour=hour_bucket)
    return dt.replace(minute=0, second=0, microsecond=0)


def _entry_buckets(entries: list[datetime], *, tolerance: timedelta) -> list[datetime]:
    if not entries:
        return []
    sorted_entries = sorted(entries)
    buckets: list[datetime] = []
    for entry in sorted_entries:
        if not buckets or entry - buckets[-1] > tolerance:
            buckets.append(entry)
    return buckets


def _match_overlap(a: list[datetime], b: list[datetime], *, tolerance: timedelta) -> int:
    if not a or not b:
        return 0
    matches = 0
    j = 0
    for left in sorted(a):
        while j < len(b) and b[j] < left - tolerance:
            j += 1
        if j < len(b) and abs((b[j] - left).total_seconds()) <= tolerance.total_seconds():
            matches += 1
    return matches


def _pair_metrics(
    left: EntrySet,
    right: EntrySet,
    *,
    tolerance: timedelta,
) -> dict[str, float | int]:
    left_b = _entry_buckets(left.entries, tolerance=tolerance)
    right_b = _entry_buckets(right.entries, tolerance=tolerance)
    shared = _match_overlap(left_b, right_b, tolerance=tolerance)
    union = len(left_b) + len(right_b) - shared
    jaccard = (shared / union) if union else 0.0
    pct_left = (shared / len(left_b) * 100.0) if left_b else 0.0
    pct_right = (shared / len(right_b) * 100.0) if right_b else 0.0
    return {
        "shared_entries": shared,
        "left_entries": len(left_b),
        "right_entries": len(right_b),
        "jaccard": round(jaccard, 4),
        "pct_of_left_also_in_right": round(pct_left, 2),
        "pct_of_right_also_in_left": round(pct_right, 2),
    }


async def _collect_oos_entries(
    spec: AgentSpec,
    *,
    symbol: str,
    timeframe: str,
    train_months: int,
    test_months: int,
    start: str | None,
    end: str | None,
) -> EntrySet:
    config_path = _resolve_config_path(spec)
    settings = load_settings(config_path)
    run_symbol = symbol or settings.trading_pairs[0]
    run_timeframe = timeframe or settings.timeframe

    result = _resolve_strategy_config(settings.strategy)
    strategy_classes = result[0]
    strategy_configs = result[1]
    aggregator_config = dict(result[2])

    raw_config = _read_yaml(config_path)
    db_config = _db_config_from_settings(settings)
    await init_pool(db_config)
    try:
        range_start, range_end = await _resolve_data_range(run_symbol, run_timeframe)
        range_start = start or range_start
        range_end = end or range_end

        windows = build_wfo_windows(
            start=range_start,
            end=range_end,
            train_months=train_months,
            test_months=test_months,
        )

        reader = IndicatorReader(db_config)
        entries: list[datetime] = []
        async with reader:
            for window in windows:
                window_config = _build_backtest_config(
                    settings=settings,
                    raw_config=raw_config,
                    symbol=run_symbol,
                    timeframe=run_timeframe,
                    start=window.test_start,
                    end=window.test_end,
                    strategy_classes=strategy_classes,
                    strategy_configs=strategy_configs,
                    aggregator_config=aggregator_config,
                    initial_capital=10000.0,
                    disable_trend_filter=False,
                    replay_sentiment_path=spec.replay_sentiment_log,
                    replay_sentiment_max_age_hours=spec.replay_sentiment_max_age_hours,
                )
                result_bt = await _run_backtest(reader, window_config)
                for trade in result_bt.trades:
                    if trade.side.lower() != "long":
                        continue
                    entries.append(_normalize_entry(trade.entry_time, timeframe=run_timeframe))
    finally:
        await close_pool()

    return EntrySet(
        label=spec.label,
        symbol=run_symbol,
        timeframe=run_timeframe,
        entries=entries,
        note=spec.note,
    )


async def _collect_live_entries(
    *,
    symbol: str,
    agent_ids: list[str],
    live_days: int,
) -> list[EntrySet]:
    pool = get_pool()
    cutoff = datetime.now(UTC) - timedelta(days=live_days)
    sets: list[EntrySet] = []
    query = """
        SELECT agent_id, entry_time
        FROM positions
        WHERE symbol = $1
          AND agent_id = ANY($2::text[])
          AND entry_time >= $3
          AND position_side = 'LONG'
        ORDER BY entry_time
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, symbol, agent_ids, cutoff)

    by_agent: dict[str, list[datetime]] = {agent_id: [] for agent_id in agent_ids}
    for row in rows:
        agent_id = str(row["agent_id"])
        entry_time = row["entry_time"]
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=UTC)
        by_agent.setdefault(agent_id, []).append(
            entry_time.replace(minute=0, second=0, microsecond=0)
        )

    for agent_id, entries in by_agent.items():
        sets.append(
            EntrySet(
                label=f"live:{agent_id}",
                symbol=symbol,
                timeframe="live",
                entries=entries,
            )
        )
    return sets


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Entry overlap report",
        "",
        f"- Symbol: `{report['symbol']}`",
        f"- Timeframe: `{report['timeframe']}`",
        f"- WFO: train={report['train_months']}mo test={report['test_months']}mo",
        f"- Tolerance: {report['tolerance_hours']}h",
        "",
        "## OOS entry counts (backtest)",
        "",
        "| Agent | Symbol | Entries | Note |",
        "|-------|--------|---------|------|",
    ]
    for row in report["entry_counts"]:
        note = row.get("note") or ""
        lines.append(f"| {row['label']} | {row['symbol']} | {row['entries']} | {note} |")

    lines.extend(["", "## Pairwise overlap (OOS backtest)", ""])
    lines.append("| A | B | Shared | Jaccard | %A in B | %B in A |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for pair in report["pairwise_oos"]:
        lines.append(
            f"| {pair['left']} | {pair['right']} | {pair['shared_entries']} | "
            f"{pair['jaccard']:.2%} | {pair['pct_of_left_also_in_right']:.1f}% | "
            f"{pair['pct_of_right_also_in_left']:.1f}% |"
        )

    if report.get("pairwise_live"):
        lines.extend(["", "## Pairwise overlap (live DB)", ""])
        lines.append("| A | B | Shared | Jaccard | %A in B | %B in A |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for pair in report["pairwise_live"]:
            lines.append(
                f"| {pair['left']} | {pair['right']} | {pair['shared_entries']} | "
                f"{pair['jaccard']:.2%} | {pair['pct_of_left_also_in_right']:.1f}% | "
                f"{pair['pct_of_right_also_in_left']:.1f}% |"
            )

    lines.extend(["", "## Interpretation", "", report.get("interpretation", ""), ""])
    return "\n".join(lines)


def _interpret(report: dict[str, Any]) -> str:
    pairs = report.get("pairwise_oos", [])
    focus = [
        p
        for p in pairs
        if "sol_1h_trend_pullback_overlay_live" in (p["left"], p["right"])
        and "sentiment_macro" in (p["left"], p["right"])
    ]
    if not focus:
        return "No live vs sentiment pair found in manifest."
    row = focus[0]
    jaccard = float(row["jaccard"])
    pct_live = float(row.get("pct_of_left_also_in_right", 0.0))
    if row["left"] != "sol_1h_trend_pullback_overlay_live":
        pct_live = float(row.get("pct_of_right_also_in_left", 0.0))
    if jaccard >= 0.35 or pct_live >= 40.0:
        return (
            "High overlap between SOL 1h trend_pullback overlay and sentiment-macro on "
            "OOS entries. Adding another SOL 1h technical agent likely concentrates risk "
            "rather than diversifying."
        )
    if jaccard >= 0.15 or pct_live >= 20.0:
        return (
            "Moderate overlap between SOL 1h overlay and sentiment-macro. Any second SOL "
            "1h candidate needs explicit independence justification and lower size."
        )
    return (
        "Low OOS entry overlap between SOL 1h overlay and sentiment-macro. A second SOL 1h "
        "agent may add diversification if it passes promotion gates independently."
    )


async def main() -> None:
    args = parse_args()
    configure_logger("INFO")
    manifest = _load_manifest(Path(args.manifest))
    specs = _parse_agents(manifest)

    symbol = args.symbol or str(manifest.get("symbol", "SOLUSDT"))
    timeframe = args.timeframe or str(manifest.get("timeframe", "1h"))
    train_months = args.train_months or int(manifest.get("train_months", 3))
    test_months = args.test_months or int(manifest.get("test_months", 2))
    tolerance = timedelta(hours=args.tolerance_hours)

    entry_sets: list[EntrySet] = []
    for spec in specs:
        entry_sets.append(
            await _collect_oos_entries(
                spec,
                symbol=symbol,
                timeframe=timeframe,
                train_months=train_months,
                test_months=test_months,
                start=None,
                end=None,
            )
        )

    sol_sets = [s for s in entry_sets if s.symbol == symbol]
    pairwise_oos = []
    for left, right in combinations(sol_sets, 2):
        metrics = _pair_metrics(left, right, tolerance=tolerance)
        pairwise_oos.append(
            {
                "left": left.label,
                "right": right.label,
                **metrics,
            }
        )

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "timeframe": timeframe,
        "train_months": train_months,
        "test_months": test_months,
        "tolerance_hours": args.tolerance_hours,
        "entry_counts": [
            {
                "label": s.label,
                "symbol": s.symbol,
                "entries": len(_entry_buckets(s.entries, tolerance=tolerance)),
                "note": s.note,
            }
            for s in entry_sets
        ],
        "pairwise_oos": pairwise_oos,
    }

    if args.include_live_db:
        db_config = {
            "host": str(os.getenv("POSTGRES_HOST", "localhost")),
            "port": int(os.getenv("POSTGRES_PORT", 5432)),
            "name": str(os.getenv("POSTGRES_DB", "marketdata")),
            "user": str(os.getenv("POSTGRES_USER", "trading")),
            "password": str(os.getenv("POSTGRES_PASSWORD", "")),
        }
        await init_pool(db_config)
        try:
            live_sets = await _collect_live_entries(
                symbol=symbol,
                agent_ids=list(args.agent_ids),
                live_days=args.live_days,
            )
            report["live_entry_counts"] = [
                {"label": s.label, "entries": len(s.entries)} for s in live_sets
            ]
            pairwise_live = []
            for left, right in combinations(live_sets, 2):
                pairwise_live.append(
                    {
                        "left": left.label,
                        "right": right.label,
                        **_pair_metrics(left, right, tolerance=tolerance),
                    }
                )
            report["pairwise_live"] = pairwise_live
        finally:
            await close_pool()

    report["interpretation"] = _interpret(report)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path = out_path.with_suffix(".md")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nWrote {out_path} and {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
