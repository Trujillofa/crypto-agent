"""Tests for EventLog core functionality."""

from __future__ import annotations

import pytest

from src.core.event_log import EventLog


class TestGetRecentByType:
    @pytest.mark.asyncio
    async def test_filters_by_event_type(self, tmp_path):
        log = EventLog("test-agent", data_dir=tmp_path)
        await log.log("sentiment_score", {"symbol": "BTCUSDT", "score": 75})
        await log.log("signal_received", {"symbol": "BTCUSDT"})
        await log.log("sentiment_score", {"symbol": "ETHUSDT", "score": 60})

        result = log.get_recent_by_type("sentiment_score")
        assert len(result) == 2
        assert all(e.type == "sentiment_score" for e in result)

    @pytest.mark.asyncio
    async def test_respects_limit(self, tmp_path):
        log = EventLog("test-agent", data_dir=tmp_path)
        for i in range(5):
            await log.log("sentiment_score", {"symbol": "BTCUSDT", "score": i})

        result = log.get_recent_by_type("sentiment_score", limit=3)
        assert len(result) == 3
        # Should return the last 3
        assert result[0].payload["score"] == 2

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_matches(self, tmp_path):
        log = EventLog("test-agent", data_dir=tmp_path)
        await log.log("signal_received", {"symbol": "BTCUSDT"})

        result = log.get_recent_by_type("sentiment_score")
        assert result == []
