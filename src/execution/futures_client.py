"""Binance USDⓈ-M Futures API client."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

from src.utils.logger import get_logger


@dataclass(frozen=True)
class FuturesOrderInfo:
    """Information about a Binance futures order."""

    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    position_side: str  # "LONG" or "SHORT"
    order_type: str  # "MARKET", "LIMIT", etc.
    quantity: float
    price: float | None
    status: str
    executed_quantity: float
    create_time: int
    reduce_only: bool  # True for close orders


@dataclass(frozen=True)
class FuturesPositionInfo:
    """Information about a futures position."""

    symbol: str
    position_side: str  # "LONG" or "SHORT"
    position_amt: float  # Positive for LONG, negative for SHORT
    entry_price: float
    mark_price: float
    liquidation_price: float
    leverage: int
    isolated_margin: float
    unrealized_pnl: float
    notional_value: float


@dataclass(frozen=True)
class FuturesAccountInfo:
    """Information about Binance Futures account."""

    total_margin_balance: float
    available_balance: float
    unrealized_pnl: float
    margin_ratio: float  # Current margin ratio


@dataclass(frozen=True)
class FundingRateInfo:
    """Current funding rate information."""

    symbol: str
    funding_rate: float
    funding_time: int  # Next funding timestamp


class BinanceFuturesClient:
    """Async Binance USDⓈ-M Futures API client.

    This client connects to fapi.binance.com for futures trading.
    On demo.binance.com, the same API keys work for both spot and futures.
    """

    BASE_URL = "https://fapi.binance.com"
    DEMO_URL = "https://demo.binance.com"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        test_mode: bool = False,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._test_mode = test_mode
        self._logger = get_logger(self.__class__.__name__)
        self._session: aiohttp.ClientSession | None = None
        # Use demo URL when in test mode (same keys work for both spot and futures on demo)
        self._base_url = self.DEMO_URL if test_mode else self.BASE_URL

    async def __aenter__(self) -> BinanceFuturesClient:
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._session is not None:
            await self._session.close()

    def _generate_signature(self, query_string: str) -> str:
        """Generate HMAC SHA256 signature for Binance API."""
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        """Make a request to Binance Futures API."""
        if self._session is None:
            raise RuntimeError("Session not initialized. Use async context manager.")

        params = dict(params) if params else {}
        url = f"{self._base_url}{endpoint}"

        headers = {
            "X-MBX-APIKEY": self._api_key,
            "Content-Type": "application/json",
        }

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000

            # Create query string for signature
            query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            signature = self._generate_signature(query_string)
            params["signature"] = signature

        # For GET requests, params in query string
        if method == "GET":
            query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            url = f"{url}?{query_string}"

            self._logger.debug(
                "Making GET request to %s with params: %s",
                endpoint,
                {k: "***" if k == "signature" else v for k, v in params.items()},
            )

            async with self._session.get(url, headers=headers) as response:
                return await self._handle_response(response)

        # For POST/DELETE requests, params in body
        else:
            self._logger.debug(
                "Making %s request to %s with params: %s",
                method,
                endpoint,
                {k: "***" if k == "signature" else v for k, v in params.items()},
            )

            if method == "POST":
                async with self._session.post(
                    url, headers=headers, data=params
                ) as response:
                    return await self._handle_response(response)
            elif method == "DELETE":
                async with self._session.delete(
                    url, headers=headers, data=params
                ) as response:
                    return await self._handle_response(response)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

    async def _handle_response(
        self, response: aiohttp.ClientResponse
    ) -> dict[str, Any]:
        """Handle API response and check for errors."""
        data = await response.json()

        if response.status >= 400:
            error_msg = data.get("msg", "Unknown error")
            error_code = data.get("code", response.status)
            raise RuntimeError(f"Binance Futures API error [{error_code}]: {error_msg}")

        return data

    async def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        """Set leverage for a symbol.

        Endpoint: POST /fapi/v1/leverage

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            leverage: Leverage level (1-20, hard cap enforced)

        Returns:
            API response with leverage details
        """
        # Hard safety cap at 20x
        if leverage > 20:
            raise ValueError(f"Leverage {leverage}x exceeds hard safety cap of 20x")

        if leverage < 1:
            raise ValueError(f"Leverage must be at least 1x")

        return await self._request(
            "POST",
            "/fapi/v1/leverage",
            params={
                "symbol": symbol,
                "leverage": leverage,
            },
            signed=True,
        )

    async def get_position_risk(self, symbol: str) -> list[FuturesPositionInfo]:
        """Get position risk information including liquidation price.

        Endpoint: GET /fapi/v2/positionRisk

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")

        Returns:
            List of FuturesPositionInfo objects
        """
        data = await self._request(
            "GET",
            "/fapi/v2/positionRisk",
            params={"symbol": symbol},
            signed=True,
        )

        positions = []
        for pos_data in data:
            # Only include positions with non-zero amount
            position_amt = float(pos_data.get("positionAmt", 0))
            if position_amt != 0:
                positions.append(
                    FuturesPositionInfo(
                        symbol=pos_data.get("symbol", symbol),
                        position_side=pos_data.get("positionSide", "LONG"),
                        position_amt=position_amt,
                        entry_price=float(pos_data.get("entryPrice", 0)),
                        mark_price=float(pos_data.get("markPrice", 0)),
                        liquidation_price=float(pos_data.get("liquidationPrice", 0)),
                        leverage=int(pos_data.get("leverage", 1)),
                        isolated_margin=float(pos_data.get("isolatedMargin", 0)),
                        unrealized_pnl=float(pos_data.get("unRealizedProfit", 0)),
                        notional_value=float(pos_data.get("notional", 0)),
                    )
                )

        return positions

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        reduce_only: bool = False,
        position_side: str = "LONG",
    ) -> FuturesOrderInfo:
        """Place a futures order.

        Endpoint: POST /fapi/v1/order

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            side: "BUY" or "SELL"
            quantity: Order quantity
            order_type: "MARKET" or "LIMIT"
            reduce_only: If True, order will only reduce position (for closes)
            position_side: "LONG" or "SHORT" (one-way mode)

        Returns:
            FuturesOrderInfo with order details
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "positionSide": position_side,
        }

        if reduce_only:
            params["reduceOnly"] = "true"

        data = await self._request(
            "POST",
            "/fapi/v1/order",
            params=params,
            signed=True,
        )

        return FuturesOrderInfo(
            order_id=str(data.get("orderId", 0)),
            symbol=data.get("symbol", symbol),
            side=data.get("side", side),
            position_side=data.get("positionSide", position_side),
            order_type=data.get("type", order_type),
            quantity=float(data.get("origQty", quantity)),
            price=float(data.get("price", 0)) if data.get("price") else None,
            status=data.get("status", "NEW"),
            executed_quantity=float(data.get("executedQty", 0)),
            create_time=int(data.get("time", 0)),
            reduce_only=reduce_only,
        )

    async def get_funding_rate(self, symbol: str) -> FundingRateInfo:
        """Get current funding rate for a symbol.

        Endpoint: GET /fapi/v1/fundingRate

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")

        Returns:
            FundingRateInfo with current rate and next funding time
        """
        data = await self._request(
            "GET",
            "/fapi/v1/fundingRate",
            params={"symbol": symbol},
            signed=False,  # Public endpoint
        )

        # Response is a list, get the most recent
        if isinstance(data, list) and len(data) > 0:
            latest = data[0]
            return FundingRateInfo(
                symbol=latest.get("symbol", symbol),
                funding_rate=float(latest.get("fundingRate", 0)),
                funding_time=int(latest.get("fundingTime", 0)),
            )
        elif isinstance(data, dict):
            return FundingRateInfo(
                symbol=data.get("symbol", symbol),
                funding_rate=float(data.get("fundingRate", 0)),
                funding_time=int(data.get("fundingTime", 0)),
            )
        else:
            raise RuntimeError(f"Unexpected funding rate response format: {data}")

    async def get_account_info(self) -> FuturesAccountInfo:
        """Get futures account information.

        Endpoint: GET /fapi/v2/account

        Returns:
            FuturesAccountInfo with margin and balance details
        """
        data = await self._request(
            "GET",
            "/fapi/v2/account",
            params={},
            signed=True,
        )

        return FuturesAccountInfo(
            total_margin_balance=float(data.get("totalMarginBalance", 0)),
            available_balance=float(data.get("availableBalance", 0)),
            unrealized_pnl=float(data.get("totalUnrealizedProfit", 0)),
            margin_ratio=float(data.get("marginRatio", 0)),
        )

    async def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Cancel a futures order.

        Endpoint: DELETE /fapi/v1/order

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            order_id: Order ID to cancel

        Returns:
            API response with cancellation details
        """
        return await self._request(
            "DELETE",
            "/fapi/v1/order",
            params={
                "symbol": symbol,
                "orderId": order_id,
            },
            signed=True,
        )
