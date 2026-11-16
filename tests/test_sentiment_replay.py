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


def raw_scorer(tmp_path, text, **kwargs):
    path = tmp_path / "events.jsonl"
    path.write_text(text, encoding="utf-8")
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
        "tainted_symbols": 0,
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
@pytest.mark.parametrize(
    "bad_row",
    [
        None,
        [],
        "text",
        42,
        {"type": "sentiment_score", "payload": ["invalid"]},
        {"type": "sentiment_score", "payload": "invalid"},
        {"type": "sentiment_score", "payload": 42},
        {
            "type": "sentiment_score",
            "ts": "9999-12-31T23:59:59-14:00",
            "payload": {"symbol": "SOLUSDT", "score": 80},
        },
    ],
)
async def test_malformed_rows_do_not_abort_or_corrupt_timeline(tmp_path, bad_row):
    # Out-of-order valid rows also prove the loader finalized its sorted index.
    # An error-source row with an unusable timestamp is not in this list: it cannot be
    # placed in time, so it conservatively extends the gap past the newest durable
    # observation instead of being dropped (see the tail-gap regressions below).
    replay = scorer(tmp_path, [event(2, 90), bad_row, event(0, 70)])
    assert replay.stats()["loaded_observations"] == 2
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=1)) == 70
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=3)) == 90


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), 101, -1, True, None, "bad", "1e999", "_1"]
)
async def test_invalid_score_starts_gap_until_valid_recovery(tmp_path, bad):
    replay = scorer(tmp_path, [event(0, 70), event(1, bad), event(2, 90)], fallback_score=48)
    assert await replay.get_score("SOLUSDT", BASE) == 70
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=1)) == 48
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=2)) == 90


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["-1e-999", "100.00000000000000000001"])
async def test_decimal_string_outside_exact_bounds_starts_gap(tmp_path, bad):
    replay = scorer(tmp_path, [event(0, 70), event(1, bad), event(2, "100")], fallback_score=48)
    assert [await replay.get_score("SOLUSDT", BASE + timedelta(hours=i)) for i in range(3)] == [
        70,
        48,
        100,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_line", ['{"type":"other","n":' + "9" * 5000 + "}", "[" * 1100 + "0" + "]" * 1100]
)
async def test_unparseable_json_row_does_not_discard_timeline(tmp_path, bad_line):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join([json.dumps(event(2, 90)), bad_line, json.dumps(event(0, 70))]),
        encoding="utf-8",
    )
    replay = ReplaySentimentScorer(path, fallback_score=48)
    assert replay.stats()["loaded_observations"] == 2
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=1)) == 70
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=3)) == 90


@pytest.mark.asyncio
async def test_decode_error_discards_partial_timeline_including_latest_lookup(tmp_path):
    path = tmp_path / "broken.jsonl"
    # Exceed the text decoder's buffer so one event is processed before failure.
    path.write_bytes((json.dumps(event(0, 70)) + "\n" + " " * 10000 + "\n").encode() + b"\xff\n")
    replay = ReplaySentimentScorer(path, fallback_score=48)
    assert replay.stats()["loaded_observations"] == 0
    assert await replay.get_score("SOLUSDT") == 48
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=1)) == 48


@pytest.mark.asyncio
async def test_replay_gap_neutral_is_not_live_degradation_buy_veto(tmp_path):
    from src.strategy.sentiment_mean_reversion import SentimentMeanReversionStrategy
    from src.strategy.signals import SignalType

    class DegradedScorer:
        degraded = True

        async def get_score(self, symbol):
            return 50

    indicators = {
        "time": BASE,
        "close_price": 100,
        "rsi_14": 20,
        "bb_lower_dist": 0,
        "bb_upper_dist": 1,
    }
    strategy = SentimentMeanReversionStrategy({"volatility_regime_filter": False})
    strategy.set_scorer(scorer(tmp_path, [event(0, None, "error_fallback")]))
    assert (await strategy.evaluate("SOLUSDT", indicators)).type == SignalType.BUY
    strategy.set_scorer(DegradedScorer())
    assert (await strategy.evaluate("SOLUSDT", indicators)).type == SignalType.HOLD


@pytest.mark.asyncio
async def test_torn_error_tail_without_newline_gaps_every_symbol(tmp_path):
    # EventLog always terminates a record with "\n", so an undecodable final line is a
    # torn append whose symbol/time is unknowable: the pre-tail score must not survive.
    torn = json.dumps(event(1, 30, "error_fallback"))[:40]
    replay = raw_scorer(
        tmp_path, json.dumps(event(0, 70, "xai_live")) + "\n" + torn, fallback_score=48
    )
    assert replay.stats()["loaded_observations"] == 1
    assert replay.stats()["tainted_symbols"] == 1
    assert [
        await replay.get_score("SOLUSDT", BASE + timedelta(hours=hour)) for hour in range(3)
    ] == [70, 48, 48]
    assert await replay.get_score("SOLUSDT") == 48


@pytest.mark.asyncio
async def test_torn_score_tail_without_newline_also_gaps(tmp_path):
    # A torn tail could have been a healthy score or an error; it cannot be replayed
    # either way, because its value and time are unknown.
    torn = json.dumps(event(1, 90, "xai_live"))[:30]
    replay = raw_scorer(tmp_path, json.dumps(event(0, 70)) + "\n" + torn, fallback_score=48)
    assert replay.stats()["loaded_observations"] == 1
    assert [
        await replay.get_score("SOLUSDT", BASE + timedelta(hours=hour)) for hour in range(2)
    ] == [70, 48]


@pytest.mark.asyncio
async def test_complete_final_record_without_newline_is_not_treated_as_torn(tmp_path):
    # Only an undecodable unterminated tail is a torn append; a complete last record
    # without its newline still carries usable data.
    replay = raw_scorer(tmp_path, json.dumps(event(0, 70, "xai_live")), fallback_score=48)
    assert replay.stats()["tainted_symbols"] == 0
    assert await replay.get_score("SOLUSDT", BASE) == 70
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=2)) == 70


@pytest.mark.asyncio
async def test_error_without_timestamp_still_gaps_symbol(tmp_path):
    replay = scorer(
        tmp_path,
        [
            event(0, 70, "xai_live"),
            {
                "type": "sentiment_score",
                "payload": {"symbol": "SOLUSDT", "source": "error_fallback"},
            },
        ],
        fallback_score=48,
    )
    assert replay.stats()["loaded_observations"] == 1
    assert replay.stats()["tainted_symbols"] == 1
    assert [
        await replay.get_score("SOLUSDT", BASE + timedelta(hours=hour)) for hour in range(3)
    ] == [70, 48, 48]
    assert await replay.get_score("SOLUSDT") == 48


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_ts", ["0001-01-01T00:00:00+14:00", "not-a-timestamp", 10**400])
async def test_error_with_unusable_timestamp_still_gaps_symbol(tmp_path, bad_ts):
    replay = scorer(
        tmp_path,
        [
            event(0, 70, "xai_live"),
            {
                "type": "sentiment_score",
                "ts": bad_ts,
                "payload": {"symbol": "SOLUSDT", "source": "xai_error_fallback"},
            },
        ],
        fallback_score=48,
    )
    assert replay.stats()["loaded_observations"] == 1
    assert [
        await replay.get_score("SOLUSDT", BASE + timedelta(hours=hour)) for hour in range(2)
    ] == [70, 48]


@pytest.mark.asyncio
async def test_unplaceable_error_time_does_not_gap_other_symbols(tmp_path):
    replay = scorer(
        tmp_path,
        [
            event(0, 70, "xai_live", "SOLUSDT"),
            event(0, 80, "xai_live", "BTCUSDT"),
            {
                "type": "sentiment_score",
                "payload": {"symbol": "SOLUSDT", "source": "error_fallback"},
            },
        ],
        fallback_score=48,
    )
    later = BASE + timedelta(hours=1)
    assert await replay.get_score("SOLUSDT", later) == 48
    assert await replay.get_score("BTCUSDT", later) == 80
    assert replay.stats()["tainted_symbols"] == 1


@pytest.mark.asyncio
async def test_error_without_symbol_gaps_every_symbol(tmp_path):
    replay = scorer(
        tmp_path,
        [
            event(0, 70, "xai_live", "SOLUSDT"),
            event(0, 80, "xai_live", "BTCUSDT"),
            {"type": "sentiment_score", "payload": {"source": "error_fallback", "score": 30}},
        ],
        fallback_score=48,
    )
    later = BASE + timedelta(hours=1)
    assert await replay.get_score("SOLUSDT", later) == 48
    assert await replay.get_score("BTCUSDT", later) == 48
    assert replay.stats()["tainted_symbols"] == 2


@pytest.mark.asyncio
async def test_non_error_row_with_unusable_timestamp_is_skipped_without_gap(tmp_path):
    # A valid-score row we cannot place in time is still only a lost observation.
    replay = scorer(
        tmp_path,
        [
            event(0, 70, "xai_live"),
            {
                "type": "sentiment_score",
                "ts": "9999-12-31T23:59:59-14:00",
                "payload": {"symbol": "SOLUSDT", "score": 80},
            },
        ],
        fallback_score=48,
    )
    assert replay.stats()["tainted_symbols"] == 0
    assert await replay.get_score("SOLUSDT", BASE + timedelta(hours=1)) == 70
