import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ingest.metrics import IngestMetrics
from src.ingest.websocket import BinanceWebSocketIngestor


class TestBinanceWebSocketIngestor:
    @pytest.fixture
    def metrics(self):
        m = MagicMock(spec=IngestMetrics)
        m.messages_total = MagicMock()
        m.errors_total = MagicMock()
        m.last_open_time = MagicMock()
        return m

    @pytest.mark.asyncio
    async def test_websocket_kline_parsing(self, metrics):
        ingestor = BinanceWebSocketIngestor(symbols=["BTCUSDT"], timeframe="1m", metrics=metrics)

        on_candle = AsyncMock()

        mock_ws = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock()

        partial_msg = {
            "e": "kline",
            "s": "BTCUSDT",
            "k": {
                "t": 1672531200000,
                "T": 1672531259999,
                "o": "100.0",
                "c": "101.0",
                "h": "102.0",
                "l": "99.0",
                "v": "1000.0",
                "x": False,  # Not closed
            },
        }

        closed_msg = {
            "e": "kline",
            "s": "BTCUSDT",
            "k": {
                "t": 1672531200000,
                "T": 1672531259999,
                "o": "100.0",
                "c": "105.0",
                "h": "106.0",
                "l": "99.0",
                "v": "2000.0",
                "x": True,  # Closed
            },
        }

        mock_ws.__aiter__.return_value = [
            MagicMock(type=1, data=json.dumps(partial_msg)),
            MagicMock(type=1, data=json.dumps(closed_msg)),
        ]

        mock_session = AsyncMock()
        mock_session.ws_connect.return_value = mock_ws

        ingestor._session = mock_session
        ingestor._running = True

        await ingestor._handle_message(json.dumps(partial_msg), on_candle)
        on_candle.assert_not_called()

        await ingestor._handle_message(json.dumps(closed_msg), on_candle)
        on_candle.assert_called_once()

        candle = on_candle.call_args[0][0]
        assert candle.symbol == "BTCUSDT"
        assert candle.close_price == 105.0
        assert candle.volume == 2000.0
