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
class OrderInfo:
    """Information about a Binance order."""

    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    order_type: str  # "MARKET", "LIMIT", "STOP", etc.
    quantity: float
    price: float | None
    status: str  # "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", etc.
    executed_quantity: float
    create_time: int


@dataclass(frozen=True)
class AccountInfo:
    """Information about Binance Spot account (USDT balances)."""

    total_balance: float  # Total USDT (free + locked)
    available_balance: float  # Free USDT available for trading


class BinancePrivateClient:
    """Async Binance Spot private API client."""

    BASE_URL = "https://api.binance.com"
    TESTNET_URL = "https://testnet.binance.vision"
    DEMO_URL = "https://demo-api.binance.com"

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
        # Use demo URL when in test mode (demo.binance.com, not testnet)
        self._base_url = self.DEMO_URL if test_mode else self.BASE_URL

    async def __aenter__(self) -> BinancePrivateClient:
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
        """Make a request to Binance API."""
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

        # For POST requests, params in body
        else:
            self._logger.debug(
                "Making %s request to %s with params: %s",
                method,
                endpoint,
                {k: "***" if k == "signature" else v for k, v in params.items()},
            )

            async with self._session.post(
                url, headers=headers, data=params
            ) as response:
                return await self._handle_response(response)

    async def _handle_response(
        self, response: aiohttp.ClientResponse
    ) -> dict[str, Any]:
        """Handle API response and check for errors."""
        data = await response.json()

        if response.status >= 400:
            error_msg = data.get("msg", "Unknown error")
            error_code = data.get("code", response.status)
            raise RuntimeError(f"Binance API error [{error_code}]: {error_msg}")

        return data

    async def get_account_info(self) -> AccountInfo:
        """Get current spot account information.

        Endpoint: GET /api/v3/account
        """
        data = await self._request("GET", "/api/v3/account", signed=True)
        balances = data.get("balances", [])
        usdt_balance = next(
            (balance for balance in balances if balance.get("asset") == "USDT"),
            None,
        )
        free = float(usdt_balance.get("free", 0)) if usdt_balance else 0.0
        locked = float(usdt_balance.get("locked", 0)) if usdt_balance else 0.0
        total = free + locked

        return AccountInfo(
            total_balance=total,
            available_balance=free,
        )

    async def get_asset_balance(self, asset: str = "USDT") -> float:
        """Get balance for a specific asset.

        Args:
            asset: Asset symbol (default: USDT)

        Returns:
            float: Available balance for the asset
        """
        data = await self._request("GET", "/api/v3/account", signed=True)
        balances = data.get("balances", [])
        asset_balance = next(
            (balance for balance in balances if balance.get("asset") == asset),
            None,
        )
        return float(asset_balance.get("free", 0)) if asset_balance else 0.0

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderInfo]:
        """Get all currently open orders.

        Endpoint: GET /api/v3/openOrders

        Args:
            symbol: Trading pair symbol. If None, returns all open orders.
        """
        params = {}
        if symbol:
            params["symbol"] = symbol

        data = await self._request("GET", "/api/v3/openOrders", params, signed=True)

        orders = []
        for order_data in data:
            orders.append(
                OrderInfo(
                    order_id=str(order_data.get("orderId", "")),
                    symbol=order_data.get("symbol", ""),
                    side=order_data.get("side", ""),
                    order_type=order_data.get("type", ""),
                    quantity=float(order_data.get("origQty", 0)),
                    price=float(order_data.get("price", 0))
                    if order_data.get("price")
                    else None,
                    status=order_data.get("status", ""),
                    executed_quantity=float(order_data.get("executedQty", 0)),
                    create_time=int(order_data.get("time", 0)),
                )
            )

        return orders

    async def place_market_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: float,
    ) -> OrderInfo:
        """Place a market order.

        Endpoint: POST /api/v3/order

        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            side: Order side ("BUY" or "SELL")
            quantity: For BUY: amount in quote asset (USDT). For SELL: amount in base asset.

        Returns:
            OrderInfo: Information about the placed order
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
        }

        # BUY uses quoteOrderQty (USDT), SELL uses quantity (base asset)
        if side == "BUY":
            params["quoteOrderQty"] = str(quantity)
        else:
            params["quantity"] = str(quantity)

        data = await self._request("POST", "/api/v3/order", params, signed=True)

        return OrderInfo(
            order_id=str(data.get("orderId", "")),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            order_type=data.get("type", ""),
            quantity=float(data.get("origQty", 0)),
            price=float(data.get("price", 0)) if data.get("price") else None,
            status=data.get("status", ""),
            executed_quantity=float(data.get("executedQty", 0)),
            create_time=int(data.get("time", 0)),
        )

    async def place_limit_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: float,
        price: float,
    ) -> OrderInfo:
        """Place a limit order.

        Endpoint: POST /api/v3/order

        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            side: Order side ("BUY" or "SELL")
            quantity: Order quantity in base asset
            price: Limit price

        Returns:
            OrderInfo: Information about the placed order
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "quantity": str(quantity),
            "price": str(price),
            "timeInForce": "GTC",  # Good Till Cancelled
        }

        data = await self._request("POST", "/api/v3/order", params, signed=True)

        return OrderInfo(
            order_id=str(data.get("orderId", "")),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            order_type=data.get("type", ""),
            quantity=float(data.get("origQty", 0)),
            price=float(data.get("price", 0)) if data.get("price") else None,
            status=data.get("status", ""),
            executed_quantity=float(data.get("executedQty", 0)),
            create_time=int(data.get("time", 0)),
        )

    async def cancel_order(self, symbol: str, order_id: str | int) -> dict[str, Any]:
        """Cancel an existing order.

        Endpoint: DELETE /api/v3/order

        Args:
            symbol: Trading pair symbol
            order_id: Order ID to cancel

        Returns:
            dict: Cancellation response from Binance
        """
        params = {
            "symbol": symbol,
            "orderId": str(order_id),
        }

        return await self._request("DELETE", "/api/v3/order", params, signed=True)

    async def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all open orders for a symbol.

        Endpoint: DELETE /api/v3/openOrders

        Args:
            symbol: Trading pair symbol

        Returns:
            int: Number of orders cancelled
        """
        params = {"symbol": symbol}

        data = await self._request("DELETE", "/api/v3/openOrders", params, signed=True)
        if isinstance(data, list):
            return len(data)
        return 0

    async def get_order_status(self, symbol: str, order_id: str | int) -> OrderInfo:
        """Get the status of an order.

        Endpoint: GET /api/v3/order

        Args:
            symbol: Trading pair symbol
            order_id: Order ID to query

        Returns:
            OrderInfo: Current order information
        """
        params = {
            "symbol": symbol,
            "orderId": str(order_id),
        }

        data = await self._request("GET", "/api/v3/order", params, signed=True)

        return OrderInfo(
            order_id=str(data.get("orderId", "")),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            order_type=data.get("type", ""),
            quantity=float(data.get("origQty", 0)),
            price=float(data.get("price", 0)) if data.get("price") else None,
            status=data.get("status", ""),
            executed_quantity=float(data.get("executedQty", 0)),
            create_time=int(data.get("time", 0)),
        )
