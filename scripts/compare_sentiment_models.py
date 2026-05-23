#!/usr/bin/env python3
"""Benchmark xAI vs DeepSeek sentiment model quality/latency for this strategy prompt.

Example:
    python scripts/compare_sentiment_models.py \
      --rounds 3 \
      --symbols BTCUSDT,ETHUSDT,SOLUSDT \
      --xai-model grok-4-1-fast-reasoning \
      --deepseek-model deepseek-v4-pro
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from openai import AsyncOpenAI


@dataclass(frozen=True)
class Target:
    name: str
    base_url: str
    model: str
    api_key_env: str


@dataclass(frozen=True)
class Observation:
    target: str
    model: str
    symbol: str
    round_index: int
    latency_ms: float
    request_ok: bool
    json_ok: bool
    score: float | None
    reason_length: int
    error: str | None


@dataclass(frozen=True)
class Summary:
    target: str
    model: str
    total: int
    request_ok_rate: float
    json_ok_rate: float
    mean_latency_ms: float
    p95_latency_ms: float
    mean_score: float | None
    mean_reason_len: float
    mean_symbol_score_std: float
    composite_score: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare sentiment model quality/latency")
    parser.add_argument("--rounds", type=int, default=3, help="Number of rounds per symbol")
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        help="Comma-separated symbols",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--xai-model", default="grok-4-1-fast-reasoning")
    parser.add_argument("--deepseek-model", default="deepseek-v4-pro")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    return parser.parse_args()


def _build_messages(symbol: str) -> list[dict[str, str]]:
    base_asset = symbol.replace("USDT", "").replace("BUSD", "")
    prompt = (
        f"Analyze the current market sentiment for {base_asset} ({symbol}) cryptocurrency. "
        "Consider recent news, social media trends, regulatory developments, and market structure. "
        'Respond with ONLY a JSON object: {"score": <number 0-100>, "reason": "<brief reason>"} '
        "where 0=extreme fear/FUD, 50=neutral, 100=extreme greed/euphoria."
    )
    return [
        {
            "role": "system",
            "content": "You are a crypto market sentiment analyst. Respond only with valid JSON.",
        },
        {"role": "user", "content": prompt},
    ]


def _parse_payload(raw: str) -> tuple[bool, float | None, int]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False, None, 0

    score_raw = parsed.get("score") if isinstance(parsed, dict) else None
    reason_raw = parsed.get("reason") if isinstance(parsed, dict) else ""
    try:
        score = float(score_raw)
    except (TypeError, ValueError):
        return False, None, 0

    score = max(0.0, min(100.0, score))
    reason_len = len(str(reason_raw))
    return True, score, reason_len


async def _query_once(
    *,
    client: AsyncOpenAI,
    model: str,
    symbol: str,
    target: str,
    round_index: int,
) -> Observation:
    messages = _build_messages(symbol)
    t0 = time.perf_counter()
    try:
        completion = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - t0) * 1000
        return Observation(
            target=target,
            model=model,
            symbol=symbol,
            round_index=round_index,
            latency_ms=latency_ms,
            request_ok=False,
            json_ok=False,
            score=None,
            reason_length=0,
            error=str(exc)[:500],
        )

    latency_ms = (time.perf_counter() - t0) * 1000
    content = ""
    choices = getattr(completion, "choices", [])
    if choices:
        message = getattr(choices[0], "message", None)
        content = (getattr(message, "content", "") or "").strip()

    json_ok, score, reason_len = _parse_payload(content)
    return Observation(
        target=target,
        model=model,
        symbol=symbol,
        round_index=round_index,
        latency_ms=latency_ms,
        request_ok=True,
        json_ok=json_ok,
        score=score,
        reason_length=reason_len,
        error=None if json_ok else "invalid_json_payload",
    )


async def run_benchmark(
    *,
    target: Target,
    symbols: list[str],
    rounds: int,
    sleep_seconds: float,
    timeout_seconds: float,
) -> list[Observation]:
    api_key = os.getenv(target.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"Missing {target.api_key_env}")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=target.base_url,
        timeout=timeout_seconds,
        max_retries=0,
    )

    observations: list[Observation] = []
    for round_index in range(rounds):
        for symbol in symbols:
            obs = await _query_once(
                client=client,
                model=target.model,
                symbol=symbol,
                target=target.name,
                round_index=round_index,
            )
            observations.append(obs)
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
    return observations


def summarize(observations: list[Observation]) -> Summary:
    if not observations:
        raise ValueError("No observations to summarize")

    target = observations[0].target
    model = observations[0].model
    total = len(observations)

    request_ok = [o for o in observations if o.request_ok]
    json_ok = [o for o in observations if o.json_ok]
    lats = [o.latency_ms for o in observations]

    p95_index = int(0.95 * (len(lats) - 1))
    p95_ms = sorted(lats)[p95_index]

    scores = [o.score for o in json_ok if o.score is not None]
    mean_score = statistics.mean(scores) if scores else None
    mean_reason_len = statistics.mean([o.reason_length for o in json_ok]) if json_ok else 0.0

    per_symbol: dict[str, list[float]] = {}
    for obs in json_ok:
        if obs.score is None:
            continue
        per_symbol.setdefault(obs.symbol, []).append(obs.score)

    symbol_stds: list[float] = []
    for symbol_scores in per_symbol.values():
        if len(symbol_scores) < 2:
            continue
        symbol_stds.append(statistics.pstdev(symbol_scores))

    mean_symbol_score_std = statistics.mean(symbol_stds) if symbol_stds else 0.0

    request_ok_rate = len(request_ok) / total
    json_ok_rate = len(json_ok) / total

    # Composite score: reliability > speed > consistency.
    reliability = 0.6 * request_ok_rate + 0.4 * json_ok_rate
    speed = 1.0 / max(1.0, statistics.mean(lats))
    consistency = 1.0 / (1.0 + mean_symbol_score_std)
    composite = (0.6 * reliability) + (0.25 * speed * 1000) + (0.15 * consistency)

    return Summary(
        target=target,
        model=model,
        total=total,
        request_ok_rate=request_ok_rate,
        json_ok_rate=json_ok_rate,
        mean_latency_ms=statistics.mean(lats),
        p95_latency_ms=p95_ms,
        mean_score=mean_score,
        mean_reason_len=mean_reason_len,
        mean_symbol_score_std=mean_symbol_score_std,
        composite_score=composite,
    )


def select_winner(summaries: list[Summary]) -> Summary:
    return sorted(
        summaries,
        key=lambda s: (
            -s.json_ok_rate,
            -s.request_ok_rate,
            s.mean_latency_ms,
            -s.composite_score,
        ),
    )[0]


async def _main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("compare_sentiment_models")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise ValueError("At least one symbol is required")

    targets = [
        Target(
            name="xai",
            base_url="https://api.x.ai/v1",
            model=args.xai_model,
            api_key_env="XAI_API_KEY",
        ),
        Target(
            name="deepseek",
            base_url="https://api.deepseek.com/v1",
            model=args.deepseek_model,
            api_key_env="DEEPSEEK_API_KEY",
        ),
    ]

    all_obs: list[Observation] = []
    summaries: list[Summary] = []

    for target in targets:
        logger.info(
            "benchmark_start target=%s model=%s rounds=%d symbols=%s",
            target.name,
            target.model,
            args.rounds,
            symbols,
        )
        observations = await run_benchmark(
            target=target,
            symbols=symbols,
            rounds=args.rounds,
            sleep_seconds=args.sleep_seconds,
            timeout_seconds=args.timeout_seconds,
        )
        all_obs.extend(observations)
        summary = summarize(observations)
        summaries.append(summary)
        logger.info("benchmark_summary %s", json.dumps(asdict(summary), sort_keys=True))

    winner = select_winner(summaries)
    logger.info(
        "benchmark_winner target=%s model=%s reason=%s",
        winner.target,
        winner.model,
        "highest_json_reliability_then_lowest_latency",
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbols": symbols,
            "rounds": args.rounds,
            "summaries": [asdict(s) for s in summaries],
            "winner": asdict(winner),
            "observations": [asdict(o) for o in all_obs],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("benchmark_saved path=%s", out_path)

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
