import json
from datetime import UTC, datetime, timedelta

import pytest

from src.backtest.sentiment_replay import ReplaySentimentScorer

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def event(hour, score, source=None, symbol="SOLUSDT"):
    payload = {"symbol": symbol, "score": score}
    if source is not None:
        payload["source"] = source
    return {
        "type": "sentiment_score",
        "ts": (BASE + timedelta(hours=hour)).isoformat(),
        "payload": payload,
    }


def scorer(tmp_path, rows, **kwargs):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return ReplaySentimentScorer(path, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["error_fallback", "xai_error_fallback"])
@pytest.mark.parametrize("max_age", [None, 3600])
async def test_error_starts_gap_until_recovery(tmp_path, source, max_age):
    replay = scorer(
        tmp_path,
        [event(0, 72, "xai_live"), event(1, 30, source), event(4, 65, "deepseek_fallback")],
        max_age_seconds=max_age,
        fallback_score=48,
    )
    assert await replay.get_score("SOLUSDT", BASE) == 72
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=1)) == 48
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=3)) == 48
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=4)) == 65
    assert replay.stats()["hits"] == 2
    assert replay.stats()["misses"] == 2
    assert replay.stats()["stale_misses"] == 0


@pytest.mark.asyncio
async def test_gap_latest_no_timestamp_symbols_and_missing_error_score(tmp_path):
    replay = scorer(
        tmp_path,
        [
            event(0, 71, "xai_live"),
            event(1, None, "error_fallback"),
            event(2, 83, "xai_live", "BTCUSDT"),
        ],
        fallback_score=44,
    )
    assert await replay.get_score("SOLUSDT") == 44
    assert await replay.get_score("SOLUSDT", BASE - timedelta(seconds=1)) == 44
    assert await replay.get_score("BTCUSDT", BASE + timedelta(hours=3)) == 83
    assert await replay.get_score("UNKNOWN") == 44
    assert replay.stats() == {
        "hits": 1,
        "misses": 3,
        "stale_misses": 0,
        "loaded_symbols": 2,
        "loaded_observations": 3,
    }


@pytest.mark.asyncio
async def test_compatibility_legacy_unknown_neutral_and_real_30(tmp_path):
    replay = scorer(
        tmp_path,
        [
            event(0, 30),
            event(1, 31, "future_provider"),
            event(2, 50, "neutral_fallback"),
            event(3, 30, "xai_live"),
        ],
    )
    assert [await replay.get_score("SOLUSDT", BASE + timedelta(hours=i)) for i in range(4)] == [
        30,
        31,
        50,
        30,
    ]
    assert await replay.get_score("SOLUSDT") == 30


@pytest.mark.asyncio
async def test_same_timestamp_last_event_wins_and_stale_boundary(tmp_path):
    replay = scorer(
        tmp_path,
        [
            event(0, 70, "xai_live"),
            event(1, 30, "error_fallback"),
            event(1, 75, "xai_live"),
            event(2, 76, "xai_live"),
            event(2, 30, "error_fallback"),
        ],
        max_age_seconds=1800,
    )
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=1, minutes=30)) == 75
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=1, minutes=30, seconds=1)) == 50
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=2)) == 50
    assert replay.stats()["stale_misses"] == 1


@pytest.mark.asyncio
async def test_strategy_clock_never_falls_through_to_future_sentiment(tmp_path):
    from src.strategy.sentiment_mean_reversion import SentimentMeanReversionStrategy

    replay = scorer(tmp_path, [event(0, 70, "xai_live"), event(2, 90, "xai_live")])
    strategy = SentimentMeanReversionStrategy()
    strategy.set_scorer(replay)
    for clock in ("2024-01-01T01:00:00+00:00", datetime(2024, 1, 1, 1)):
        assert await strategy._get_sentiment("SOLUSDT", {"time": clock}) == 70
    for indicators in ({}, {"time": None}, {"time": "invalid"}):
        assert await strategy._get_sentiment("SOLUSDT", indicators) == 50
    assert replay.stats()["misses"] == 3


@pytest.mark.asyncio
async def test_mixed_clock_offsets_are_sorted_and_equal_instants_keep_log_order(tmp_path):
    rows = [event(0, 70, "xai_live"), event(1, None, "error_fallback"), event(1, 75, "xai_live")]
    rows[0]["ts"] = "2024-01-01T00:00:00"  # legacy UTC-naive
    rows[1]["ts"] = "2024-01-01T03:00:00+02:00"
    rows[2]["ts"] = "2024-01-01T01:00:00Z"
    replay = scorer(tmp_path, rows)
    assert await replay.get_score("SOLUSDT", "2024-01-01T00:59:59Z") == 70
    assert await replay.get_score("SOLUSDT", "2024-01-01T01:00:00Z") == 75
    assert replay.stats()["loaded_observations"] == 3
