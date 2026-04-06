from __future__ import annotations

import hashlib
import hmac
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
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
    executed_price: float | None = None


@dataclass(frozen=True)
class AccountInfo:
    """Information about Binance Spot account (USDT balances)."""

    total_balance: float  # Total USDT (free + locked)
    available_balance: float  # Free USDT available for trading


class BinanceApiError(RuntimeError):
    """Binance API error with code for retry handling."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Binance API error [{code}]: {message}")
        self.code = code
        self.message = message


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
        self._base_url = self.DEMO_URL if test_mode else self.BASE_URL
        self._time_offset_ms = 0
        self._last_time_sync = 0.0
        self._time_sync_interval_seconds = 60.0
        # LOT_SIZE filters: {symbol: {"stepSize": float, "minQty": float}}
        self._symbol_filters: dict[str, dict[str, float]] = {}
        self._lot_size_cache: dict[str, tuple[Decimal, Decimal, Decimal]] = {}

    async def __aenter__(self) -> BinancePrivateClient:
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._session is not None:
            await self._session.close()

    async def load_exchange_info(self, symbols: list[str] | None = None) -> None:
        """Fetch LOT_SIZE filters from /api/v3/exchangeInfo and cache them.

        Args:
            symbols: Optional list of symbols to filter. If None, loads all.
        """
        if self._session is None:
            raise RuntimeError("Session not initialized. Use async context manager.")

        url = f"{self._base_url}/api/v3/exchangeInfo"
        if symbols:
            url += f"?symbols=%5B{','.join(f'%22{s}%22' for s in symbols)}%5D"

        async with self._session.get(url) as resp:
            data = await resp.json()

        for sym_info in data.get("symbols", []):
            symbol = sym_info["symbol"]
            for filt in sym_info.get("filters", []):
                if filt["filterType"] == "LOT_SIZE":
                    self._symbol_filters[symbol] = {
                        "stepSize": float(filt["stepSize"]),
                        "minQty": float(filt["minQty"]),
                    }
                    break

        self._logger.info("Loaded LOT_SIZE filters for %d symbols", len(self._symbol_filters))

    def format_quantity(self, symbol: str, quantity: float) -> str:
        """Round quantity to the symbol's LOT_SIZE stepSize.

        Returns the quantity as a string with correct decimal places.
        """
        filt = self._symbol_filters.get(symbol)
        if not filt:
            # Fallback: truncate to 8 decimal places
            return f"{quantity:.8f}".rstrip("0").rstrip(".")

        step = filt["stepSize"]
        min_qty = filt["minQty"]

        # Truncate to step size (floor, not round, to avoid exceeding balance)
        precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
        truncated = math.floor(quantity / step) * step

        if truncated < min_qty:
            return "0"

        if precision == 0:
            return str(int(truncated))
        return f"{truncated:.{precision}f}"

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
        _retry: bool = True,
    ) -> dict[str, Any]:
        """Make a request to Binance API."""
        if self._session is None:
            raise RuntimeError("Session not initialized. Use async context manager.")

        base_params = dict(params) if params else {}
        params = dict(base_params)
        url = f"{self._base_url}{endpoint}"

        headers = {
            "X-MBX-APIKEY": self._api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        if signed:
            await self._ensure_time_sync()
            params["timestamp"] = self._current_timestamp_ms()
            params["recvWindow"] = 5000

            # Create query string for signature
            query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            signature = self._generate_signature(query_string)
            params["signature"] = signature

        try:
            # For GET requests, params in query string
            if method == "GET":
                query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
                url = f"{url}?{query_string}" if query_string else url

                self._logger.debug(
                    "Making GET request to %s with params: %s",
                    endpoint,
                    {k: "***" if k == "signature" else v for k, v in params.items()},
                )

                async with self._session.get(url, headers=headers) as response:
                    return await self._handle_response(response)

            # For POST requests, params in body
            query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            self._logger.debug(
                "Making %s request to %s with params: %s",
                method,
                endpoint,
                {k: "***" if k == "signature" else v for k, v in params.items()},
            )

            async with self._session.post(url, headers=headers, data=query_string) as response:
                return await self._handle_response(response)
        except BinanceApiError as exc:
            if signed and _retry and exc.code == -1021:
                self._logger.warning("Binance timestamp rejected. Resyncing time and retrying.")
                await self._sync_time(force=True)
                return await self._request(
                    method,
                    endpoint,
                    base_params,
                    signed=signed,
                    _retry=False,
                )
            raise

    async def _handle_response(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        """Handle API response and check for errors."""
        data = await response.json()

        if response.status >= 400:
            error_msg = data.get("msg", "Unknown error")
            error_code = int(data.get("code", response.status))
            raise BinanceApiError(error_code, error_msg)

        return data

    def _current_timestamp_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    async def _ensure_time_sync(self) -> None:
        if (time.time() - self._last_time_sync) < self._time_sync_interval_seconds:
            return
        await self._sync_time(force=False)

    async def _sync_time(self, force: bool) -> None:
        if self._session is None:
            raise RuntimeError("Session not initialized. Use async context manager.")
        if not force and (time.time() - self._last_time_sync) < self._time_sync_interval_seconds:
            return

        url = f"{self._base_url}/api/v3/time"
        async with self._session.get(url) as response:
            data = await response.json()
            server_time = int(data.get("serverTime", 0))
            if server_time:
                local_time = int(time.time() * 1000)
                self._time_offset_ms = server_time - local_time
                self._last_time_sync = time.time()
                self._logger.debug("Synced Binance server time. Offset=%sms", self._time_offset_ms)

    async def _get_lot_size_filter(self, symbol: str) -> tuple[Decimal, Decimal, Decimal]:
        cached = self._lot_size_cache.get(symbol)
        if cached is not None:
            return cached

        data = await self._request(
            "GET",
            "/api/v3/exchangeInfo",
            params={"symbol": symbol},
            signed=False,
        )
        symbols = data.get("symbols", [])
        if not symbols:
            raise RuntimeError(f"No exchange info found for {symbol}")

        filters = symbols[0].get("filters", [])
        lot_size = next(
            (flt for flt in filters if flt.get("filterType") == "LOT_SIZE"),
            None,
        )
        if lot_size is None:
            raise RuntimeError(f"LOT_SIZE filter missing for {symbol}")

        step_size = Decimal(str(lot_size.get("stepSize", "0.00000001")))
        min_qty = Decimal(str(lot_size.get("minQty", "0")))
        max_qty = Decimal(str(lot_size.get("maxQty", "99999999")))
        parsed = (step_size, min_qty, max_qty)
        self._lot_size_cache[symbol] = parsed
        return parsed

    async def _format_sell_quantity(self, symbol: str, quantity: float) -> str:
        normalized = await self.normalize_sell_quantity(symbol, quantity)
        if normalized is None:
            raise RuntimeError(
                f"SELL quantity {quantity} for {symbol} is below min LOT_SIZE after normalization"
            )
        return normalized

    async def normalize_sell_quantity(self, symbol: str, quantity: float) -> str | None:
        step_size, min_qty, max_qty = await self._get_lot_size_filter(symbol)

        qty = Decimal(str(quantity))
        if qty > max_qty:
            qty = max_qty

        steps = (qty / step_size).to_integral_value(rounding=ROUND_DOWN)
        normalized_qty = steps * step_size

        if normalized_qty < min_qty or normalized_qty <= 0:
            return None

        return format(normalized_qty.normalize(), "f")

    async def get_all_balances(self) -> dict[str, float]:
        """Get all non-zero asset balances (free + locked).

        Returns:
            Dict mapping asset name to total balance, e.g. {"BTC": 0.5, "USDT": 1000.0}
        """
        data = await self._request("GET", "/api/v3/account", signed=True)
        result: dict[str, float] = {}
        for bal in data.get("balances", []):
            total = float(bal.get("free", 0)) + float(bal.get("locked", 0))
            if total > 0:
                result[bal["asset"]] = total
        return result

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
                    price=(float(order_data.get("price", 0)) if order_data.get("price") else None),
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
            params["quantity"] = await self._format_sell_quantity(symbol, quantity)

        data = await self._request("POST", "/api/v3/order", params, signed=True)
        executed_qty = float(data.get("executedQty", 0))
        cum_quote = float(data.get("cummulativeQuoteQty", 0))
        executed_price = cum_quote / executed_qty if executed_qty > 0 else None

        return OrderInfo(
            order_id=str(data.get("orderId", "")),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            order_type=data.get("type", ""),
            quantity=float(data.get("origQty", 0)),
            price=float(data.get("price", 0)) if data.get("price") else None,
            status=data.get("status", ""),
            executed_quantity=executed_qty,
            executed_price=executed_price,
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
        executed_qty = float(data.get("executedQty", 0))
        cum_quote = float(data.get("cummulativeQuoteQty", 0))
        executed_price = cum_quote / executed_qty if executed_qty > 0 else None

        return OrderInfo(
            order_id=str(data.get("orderId", "")),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            order_type=data.get("type", ""),
            quantity=float(data.get("origQty", 0)),
            price=float(data.get("price", 0)) if data.get("price") else None,
            status=data.get("status", ""),
            executed_quantity=executed_qty,
            executed_price=executed_price,
            create_time=int(data.get("time", 0)),
        )
