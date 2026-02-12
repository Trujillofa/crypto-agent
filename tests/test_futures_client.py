"""Tests for BinanceFuturesClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.futures_client import (
    BinanceFuturesClient,
    FuturesOrderInfo,
    FuturesPositionInfo,
    FuturesAccountInfo,
    FundingRateInfo,
)


class TestBinanceFuturesClient:
    """Test suite for BinanceFuturesClient."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        return BinanceFuturesClient(
            api_key="test_key",
            api_secret="test_secret",
            test_mode=True,
        )

    def _mock_response(self, status: int, json_data):
        """Create a mock aiohttp response as an async context manager."""
        mock_resp = MagicMock()
        mock_resp.status = status
        mock_resp.json = AsyncMock(return_value=json_data)
        # Return an async context manager that yields this response
        return AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        )

    @pytest.mark.asyncio
    async def test_client_initialization(self, client):
        """Test client initializes correctly."""
        assert client._api_key == "test_key"
        assert client._api_secret == "test_secret"
        assert client._test_mode is True
        assert client._base_url == BinanceFuturesClient.DEMO_URL

    @pytest.mark.asyncio
    async def test_set_leverage_success(self, client):
        """Test setting leverage."""
        mock_session = MagicMock()
        mock_session.post = MagicMock(
            return_value=self._mock_response(
                200,
                {
                    "symbol": "BTCUSDT",
                    "leverage": 10,
                    "maxNotionalValue": "1000000",
                },
            )
        )
        client._session = mock_session

        result = await client.set_leverage("BTCUSDT", 10)

        assert result["leverage"] == 10
        assert result["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_set_leverage_rejects_over_20x(self, client):
        """Test that leverage > 20x is rejected."""
        with pytest.raises(ValueError) as exc_info:
            await client.set_leverage("BTCUSDT", 50)

        assert "exceeds hard safety cap of 20x" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_set_leverage_rejects_under_1x(self, client):
        """Test that leverage < 1x is rejected."""
        with pytest.raises(ValueError) as exc_info:
            await client.set_leverage("BTCUSDT", 0)

        assert "at least 1x" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_position_risk(self, client):
        """Test getting position risk with liquidation price."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=self._mock_response(
                200,
                [
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "LONG",
                        "positionAmt": "0.1",
                        "entryPrice": "50000.00",
                        "markPrice": "51000.00",
                        "liquidationPrice": "45000.00",
                        "leverage": "10",
                        "isolatedMargin": "500.00",
                        "unRealizedProfit": "100.00",
                        "notional": "5100.00",
                    }
                ],
            )
        )
        client._session = mock_session

        positions = await client.get_position_risk("BTCUSDT")

        assert len(positions) == 1
        pos = positions[0]
        assert pos.symbol == "BTCUSDT"
        assert pos.position_side == "LONG"
        assert pos.position_amt == 0.1
        assert pos.entry_price == 50000.00
        assert pos.liquidation_price == 45000.00
        assert pos.leverage == 10

    @pytest.mark.asyncio
    async def test_get_position_risk_empty(self, client):
        """Test getting position risk with no open positions."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=self._mock_response(
                200,
                [
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "LONG",
                        "positionAmt": "0",
                        "entryPrice": "0.00",
                    }
                ],
            )
        )
        client._session = mock_session

        positions = await client.get_position_risk("BTCUSDT")

        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_place_market_order(self, client):
        """Test placing a market order."""
        mock_session = MagicMock()
        mock_session.post = MagicMock(
            return_value=self._mock_response(
                200,
                {
                    "orderId": 12345,
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "origQty": "0.1",
                    "price": "0",
                    "status": "FILLED",
                    "executedQty": "0.1",
                    "time": 1234567890,
                },
            )
        )
        client._session = mock_session

        order = await client.place_order(
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.1,
            order_type="MARKET",
            position_side="LONG",
        )

        assert isinstance(order, FuturesOrderInfo)
        assert order.order_id == "12345"
        assert order.symbol == "BTCUSDT"
        assert order.side == "BUY"
        assert order.position_side == "LONG"
        assert order.order_type == "MARKET"
        assert order.quantity == 0.1
        assert order.status == "FILLED"
        assert order.reduce_only is False

    @pytest.mark.asyncio
    async def test_place_reduce_only_order(self, client):
        """Test placing a reduce-only order (for closing positions)."""
        mock_session = MagicMock()
        mock_session.post = MagicMock(
            return_value=self._mock_response(
                200,
                {
                    "orderId": 12346,
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "origQty": "0.1",
                    "status": "FILLED",
                    "executedQty": "0.1",
                    "time": 1234567890,
                },
            )
        )
        client._session = mock_session

        order = await client.place_order(
            symbol="BTCUSDT",
            side="SELL",
            quantity=0.1,
            order_type="MARKET",
            position_side="LONG",
            reduce_only=True,
        )

        assert order.reduce_only is True
        assert order.side == "SELL"

    @pytest.mark.asyncio
    async def test_get_funding_rate(self, client):
        """Test getting current funding rate."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=self._mock_response(
                200,
                [
                    {
                        "symbol": "BTCUSDT",
                        "fundingRate": "0.00010000",
                        "fundingTime": 1234567890000,
                    }
                ],
            )
        )
        client._session = mock_session

        funding = await client.get_funding_rate("BTCUSDT")

        assert isinstance(funding, FundingRateInfo)
        assert funding.symbol == "BTCUSDT"
        assert funding.funding_rate == 0.0001
        assert funding.funding_time == 1234567890000

    @pytest.mark.asyncio
    async def test_get_account_info(self, client):
        """Test getting futures account information."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=self._mock_response(
                200,
                {
                    "totalMarginBalance": "10000.00",
                    "availableBalance": "5000.00",
                    "totalUnrealizedProfit": "100.00",
                    "marginRatio": "0.05",
                },
            )
        )
        client._session = mock_session

        account = await client.get_account_info()

        assert isinstance(account, FuturesAccountInfo)
        assert account.total_margin_balance == 10000.00
        assert account.available_balance == 5000.00
        assert account.unrealized_pnl == 100.00
        assert account.margin_ratio == 0.05

    @pytest.mark.asyncio
    async def test_api_error_handling(self, client):
        """Test handling of API errors."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=self._mock_response(
                400,
                {
                    "code": -2015,
                    "msg": "Invalid API-key, IP, or permissions for action",
                },
            )
        )
        client._session = mock_session

        with pytest.raises(RuntimeError) as exc_info:
            await client.get_account_info()

        assert "Invalid API-key" in str(exc_info.value)
        assert "-2015" in str(exc_info.value)
