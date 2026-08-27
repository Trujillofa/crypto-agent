#!/usr/bin/env python3
"""Sentiment monitoring report — reads event log JSONL and summarizes provider activity.

Usage:
    # From a local JSONL file:
    python scripts/sentiment_report.py data/event_log_sentiment-macro-bot.jsonl

    # Pipe from server:
    ssh crypto-agent "docker exec crypto-agent-agent_sentiment_macro-1 cat /app/data/event_log_sentiment-macro-bot.jsonl" | python scripts/sentiment_report.py -

    # Fetch from server automatically (requires ssh crypto-agent configured):
    python scripts/sentiment_report.py --remote
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.strategy.sentiment_mean_reversion import (
    SENTIMENT_ERROR_SOURCES,
    is_answered_sentiment_source,
)


@dataclass
class SentimentObs:
    ts: datetime
    symbol: str
    score: float
    source: str
    provider: str = ""
    model: str = ""
    error: str = ""


@dataclass
class Report:
    observations: list[SentimentObs] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.observations)

    def by_symbol(self) -> dict[str, list[SentimentObs]]:
        groups: dict[str, list[SentimentObs]] = defaultdict(list)
        for obs in self.observations:
            groups[obs.symbol].append(obs)
        return dict(groups)

    def source_counts(self) -> Counter[str]:
        return Counter(obs.source for obs in self.observations)

    def time_range(self) -> tuple[datetime | None, datetime | None]:
        if not self.observations:
            return None, None
        return self.observations[0].ts, self.observations[-1].ts


def parse_jsonl(lines: list[str]) -> Report:
    report = Report()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "sentiment_score":
            continue
        payload = event.get("payload", {})
        ts_str = event.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        report.observations.append(
            SentimentObs(
                ts=ts,
                symbol=payload.get("symbol", "?"),
                score=float(payload.get("score", 50.0)),
                source=payload.get("source", "unknown"),
                provider=payload.get("provider", ""),
                model=payload.get("model", ""),
                error=payload.get("error", ""),
            )
        )
    report.observations.sort(key=lambda o: o.ts)
    return report


def bucket_label(score: float) -> str:
    if score < 20:
        return "0-20  (panic)"
    if score < 35:
        return "20-35 (bearish)"
    if score < 50:
        return "35-50 (cautious)"
    if score < 65:
        return "50-65 (neutral+)"
    if score < 80:
        return "65-80 (bullish)"
    return "80-100 (euphoric)"


def print_report(report: Report) -> None:
    if report.total == 0:
        print("No sentiment_score events found.")
        return

    first, last = report.time_range()
    assert first and last
    hours = max(0.01, (last - first).total_seconds() / 3600)

    print("=" * 60)
    print("  SENTIMENT MONITORING REPORT")
    print("=" * 60)
    print()
    print(f"  Period:       {first:%Y-%m-%d %H:%M} → {last:%Y-%m-%d %H:%M} UTC")
    print(f"  Duration:     {hours:.1f} hours")
    print(f"  Observations: {report.total} ({report.total / hours:.1f}/hr)")

    # Model info
    models = {obs.model for obs in report.observations if obs.model}
    if models:
        print(f"  Model:        {', '.join(models)}")
    print()

    # Source breakdown
    print("── Source Breakdown ─────────────────────────────────────")
    sources = report.source_counts()
    for source, count in sources.most_common():
        pct = count / report.total * 100
        bar = "█" * int(pct / 2)
        print(f"  {source:<22} {count:>5}  ({pct:5.1f}%)  {bar}")
    print()

    # Per-symbol stats
    print("── Per-Symbol Stats ────────────────────────────────────")
    by_sym = report.by_symbol()
    for symbol in sorted(by_sym):
        obs_list = by_sym[symbol]
        scores = [o.score for o in obs_list]
        avg = sum(scores) / len(scores)
        mn, mx = min(scores), max(scores)
        latest = obs_list[-1]
        spread = mx - mn
        print(f"  {symbol}")
        print(f"    Count:   {len(scores)}")
        print(f"    Latest:  {latest.score:.0f}  ({latest.source}, {latest.ts:%H:%M})")
        print(f"    Avg:     {avg:.1f}")
        print(f"    Range:   {mn:.0f} – {mx:.0f}  (spread: {spread:.0f})")
        print()

    # Score distribution
    print("── Score Distribution ──────────────────────────────────")
    buckets: Counter[str] = Counter()
    for obs in report.observations:
        buckets[bucket_label(obs.score)] += 1
    for label in [
        "0-20  (panic)",
        "20-35 (bearish)",
        "35-50 (cautious)",
        "50-65 (neutral+)",
        "65-80 (bullish)",
        "80-100 (euphoric)",
    ]:
        count = buckets.get(label, 0)
        pct = count / report.total * 100
        bar = "█" * int(pct / 2)
        print(f"  {label}  {count:>5}  ({pct:5.1f}%)  {bar}")
    print()

    # Errors
    errors = [obs for obs in report.observations if obs.error]
    if errors:
        print("── Errors ──────────────────────────────────────────────")
        print(f"  Total error fallbacks: {len(errors)}")
        for e in errors[-5:]:
            print(f"    {e.ts:%H:%M} {e.symbol}: {e.error[:80]}")
        print()

    # Timeline (last 10 observations)
    print("── Latest Observations ─────────────────────────────────")
    for obs in report.observations[-10:]:
        if is_answered_sentiment_source(obs.source):
            emoji = "🟢"
        elif obs.source == "neutral_fallback":
            emoji = "🟡"
        else:
            emoji = "🔴"
        print(f"  {emoji} {obs.ts:%H:%M}  {obs.symbol:<10} {obs.score:>5.0f}  ({obs.source})")
    print()

    # Health assessment
    print("── Health Assessment ───────────────────────────────────")
    live_count = sum(1 for obs in report.observations if is_answered_sentiment_source(obs.source))
    error_count = sum(1 for obs in report.observations if obs.source in SENTIMENT_ERROR_SOURCES)
    live_pct = live_count / report.total * 100
    error_pct = error_count / report.total * 100
    neutral_pct = sources.get("neutral_fallback", 0) / report.total * 100

    all_scores = [o.score for o in report.observations]
    score_spread = max(all_scores) - min(all_scores)
    stuck_at_50 = sum(1 for s in all_scores if s == 50.0) / len(all_scores) * 100

    issues = []
    if live_pct < 80:
        issues.append(f"Low live rate ({live_pct:.0f}%) — provider may be degraded")
    if error_pct > 10:
        issues.append(f"High error rate ({error_pct:.0f}%) — check sentiment API")
    if neutral_pct > 50:
        issues.append(
            f"Mostly neutral fallback ({neutral_pct:.0f}%) — provider may not be configured"
        )
    if score_spread < 10:
        issues.append(
            f"Low score variation (spread={score_spread:.0f}) — sentiment may not be useful"
        )
    if stuck_at_50 > 30:
        issues.append(f"Scores stuck at 50 ({stuck_at_50:.0f}%) — likely fallback")

    if issues:
        for issue in issues:
            print(f"  ⚠️  {issue}")
    else:
        print(f"  ✅ Healthy — {live_pct:.0f}% live, spread={score_spread:.0f}, {report.total} obs")
    print()


def fetch_remote() -> list[str]:
    """Fetch event log from server via SSH + docker exec."""
    cmd = [
        "ssh",
        "crypto-agent",
        "docker exec crypto-agent-agent_sentiment_macro-1 "
        "cat /app/data/event_log_sentiment-macro-bot.jsonl",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"SSH error: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        return result.stdout.splitlines()
    except subprocess.TimeoutExpired:
        print("SSH timeout after 30s", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("ssh not found — install OpenSSH", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--remote":
        lines = fetch_remote()
    elif arg == "-":
        lines = sys.stdin.readlines()
    else:
        path = Path(arg)
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(1)
        lines = path.read_text().splitlines()

    report = parse_jsonl(lines)
    print_report(report)


if __name__ == "__main__":
    main()
