"""Binance USDⓈ-M Futures API client."""

from __future__ import annotations

import hashlib
import hmac
import math
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


class BinanceFuturesApiError(RuntimeError):
    """Binance Futures API error with code for retry handling."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Binance Futures API error [{code}]: {message}")
        self.code = code
        self.message = message


class BinanceFuturesClient:
    """Async Binance USDⓈ-M Futures API client.

    This client connects to fapi.binance.com for futures trading.
    Demo/testnet uses demo-fapi.binance.com.
    """

    BASE_URL = "https://fapi.binance.com"
    DEMO_URL = "https://demo-fapi.binance.com"

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
        self._time_offset_ms = 0
        self._last_time_sync = 0.0
        self._time_sync_interval_seconds = 60.0
        self._symbol_filters: dict[str, dict[str, float]] = {}
        self._price_filters: dict[str, dict[str, float]] = {}

    async def __aenter__(self) -> BinanceFuturesClient:
        self._session = aiohttp.ClientSession()
        return self

    def format_price(self, symbol: str, price: float) -> str:
        """Round price to the symbol's PRICE_FILTER tickSize."""
        filt = self._price_filters.get(symbol)
        if not filt:
            return f"{price:.2f}"
        tick = filt["tickSize"]
        precision = max(0, int(round(-math.log10(tick)))) if tick < 1 else 0
        rounded = round(price / tick) * tick
        return f"{rounded:.{precision}f}"

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._session is not None:
            await self._session.close()

    async def load_exchange_info(self, symbols: list[str] | None = None) -> None:
        """Fetch LOT_SIZE filters from /fapi/v1/exchangeInfo and cache them."""
        if self._session is None:
            raise RuntimeError("Session not initialized. Use async context manager.")

        url = f"{self._base_url}/fapi/v1/exchangeInfo"
        async with self._session.get(url) as resp:
            data = await resp.json()

        for sym_info in data.get("symbols", []):
            symbol = sym_info["symbol"]
            if symbols and symbol not in symbols:
                continue
            for filt in sym_info.get("filters", []):
                if filt["filterType"] == "LOT_SIZE":
                    self._symbol_filters[symbol] = {
                        "stepSize": float(filt["stepSize"]),
                        "minQty": float(filt["minQty"]),
                    }
                elif filt["filterType"] == "PRICE_FILTER":
                    self._price_filters[symbol] = {
                        "tickSize": float(filt["tickSize"]),
                    }

        self._logger.info(
            "Loaded futures LOT_SIZE filters for %d symbols",
            len(self._symbol_filters),
        )

    def get_step_size(self, symbol: str) -> float:
        """Return the LOT_SIZE stepSize for a symbol, or 0.0 if unknown."""
        filt = self._symbol_filters.get(symbol)
        return filt["stepSize"] if filt else 0.0

    def format_quantity(self, symbol: str, quantity: float) -> str:
        """Round quantity to the symbol's LOT_SIZE stepSize."""
        filt = self._symbol_filters.get(symbol)
        if not filt:
            return f"{quantity:.8f}".rstrip("0").rstrip(".")

        step = filt["stepSize"]
        min_qty = filt["minQty"]
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
        """Make a request to Binance Futures API.

        Binance Futures API expects all signed parameters in the query string,
        even for POST/DELETE requests (unlike the Spot API which accepts form body).
        """
        if self._session is None:
            raise RuntimeError("Session not initialized. Use async context manager.")

        base_params = dict(params) if params else {}
        params = dict(base_params)
        url = f"{self._base_url}{endpoint}"

        headers = {"X-MBX-APIKEY": self._api_key}

        if signed:
            await self._ensure_time_sync()
            params["timestamp"] = self._current_timestamp_ms()
            params["recvWindow"] = 5000

            # Create query string for signature
            query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            signature = self._generate_signature(query_string)
            params["signature"] = signature

        # All Futures API requests use query string params (not body)
        query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        full_url = f"{url}?{query_string}" if query_string else url

        self._logger.debug(
            "Making %s request to %s with params: %s",
            method,
            endpoint,
            {k: "***" if k == "signature" else v for k, v in params.items()},
        )

        try:
            if method == "GET":
                async with self._session.get(full_url, headers=headers) as response:
                    return await self._handle_response(response)
            if method == "POST":
                async with self._session.post(full_url, headers=headers) as response:
                    return await self._handle_response(response)
            if method == "DELETE":
                async with self._session.delete(full_url, headers=headers) as response:
                    return await self._handle_response(response)
            raise ValueError(f"Unsupported HTTP method: {method}")
        except BinanceFuturesApiError as exc:
            if signed and _retry and exc.code == -1021:
                self._logger.warning(
                    "Binance futures timestamp rejected. Resyncing time and retrying."
                )
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
            raise BinanceFuturesApiError(error_code, error_msg)

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

        url = f"{self._base_url}/fapi/v1/time"
        async with self._session.get(url) as response:
            data = await response.json()
            server_time = int(data.get("serverTime", 0))
            if server_time:
                local_time = int(time.time() * 1000)
                self._time_offset_ms = server_time - local_time
                self._last_time_sync = time.time()
                self._logger.debug(
                    "Synced Binance futures server time. Offset=%sms",
                    self._time_offset_ms,
                )

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
            raise ValueError("Leverage must be at least 1x")

        return await self._request(
            "POST",
            "/fapi/v1/leverage",
            params={
                "symbol": symbol,
                "leverage": leverage,
            },
            signed=True,
        )

    async def get_position_mode(self) -> str:
        data = await self._request(
            "GET",
            "/fapi/v1/positionSide/dual",
            params={},
            signed=True,
        )
        dual_side = str(data.get("dualSidePosition", "false")).lower() == "true"
        return "hedge" if dual_side else "one-way"

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
        quantity: float = 0.0,
        order_type: str = "MARKET",
        reduce_only: bool = False,
        position_side: str = "LONG",
        stop_price: float | None = None,
        close_position: bool = False,
    ) -> FuturesOrderInfo:
        """Place a futures order.

        Endpoint: POST /fapi/v1/order

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            side: "BUY" or "SELL"
            quantity: Order quantity (ignored when close_position=True)
            order_type: "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET", etc.
            reduce_only: Partial-close flag; mutually exclusive with close_position
            position_side: "BOTH" (one-way mode) or "LONG"/"SHORT" (hedge mode)
            stop_price: Trigger price for conditional orders
            close_position: Use closePosition=true to close the full open position
                (required by Binance for STOP_MARKET/TAKE_PROFIT_MARKET — sending
                quantity+reduceOnly returns -4120 on those order types)

        Returns:
            FuturesOrderInfo with order details
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "positionSide": position_side,
        }

        if close_position:
            # closePosition=true closes the entire position; Binance rejects quantity/reduceOnly
            params["closePosition"] = "true"
        else:
            formatted_qty = self.format_quantity(symbol, quantity)
            if formatted_qty == "0":
                raise BinanceFuturesApiError(-1, f"Quantity {quantity} below minimum for {symbol}")
            params["quantity"] = formatted_qty
            if reduce_only:
                params["reduceOnly"] = "true"

        if stop_price is not None:
            params["stopPrice"] = self.format_price(symbol, stop_price)
            params["workingType"] = "MARK_PRICE"
            params["priceProtect"] = "TRUE"

        _CONDITIONAL_TYPES = ("STOP_MARKET", "TAKE_PROFIT_MARKET")
        try:
            data = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
        except BinanceFuturesApiError as exc:
            # Some account types (e.g. Portfolio Margin) reject conditional order types on
            # /fapi/v1/order with -4120 and require the Algo Order API instead.
            if exc.code != -4120 or order_type not in _CONDITIONAL_TYPES:
                raise
            self._logger.info(
                "Retrying %s %s via Algo Order API (/fapi/v1/order/algo)", order_type, symbol
            )
            data = await self._request("POST", "/fapi/v1/order/algo", params=params, signed=True)

        avg_price = float(data.get("avgPrice", 0)) if data.get("avgPrice") else None
        price = float(data.get("price", 0)) if data.get("price") else None
        resolved_price = avg_price if avg_price and avg_price > 0 else price

        return FuturesOrderInfo(
            order_id=str(data.get("orderId", data.get("algoId", 0))),
            symbol=data.get("symbol", symbol),
            side=data.get("side", side),
            position_side=data.get("positionSide", position_side),
            order_type=data.get("type", order_type),
            quantity=float(data.get("origQty", quantity)),
            price=resolved_price,
            status=data.get("status", "NEW"),
            executed_quantity=float(data.get("executedQty", 0)),
            create_time=int(data.get("time", 0)),
            reduce_only=reduce_only,
        )

    async def get_order_status(self, symbol: str, order_id: str) -> FuturesOrderInfo:
        data = await self._request(
            "GET",
            "/fapi/v1/order",
            params={"symbol": symbol, "orderId": order_id},
            signed=True,
        )
        avg_price = float(data.get("avgPrice", 0)) if data.get("avgPrice") else None
        price = float(data.get("price", 0)) if data.get("price") else None
        resolved_price = avg_price if avg_price and avg_price > 0 else price
        return FuturesOrderInfo(
            order_id=str(data.get("orderId", order_id)),
            symbol=data.get("symbol", symbol),
            side=data.get("side", "BUY"),
            position_side=data.get("positionSide", "BOTH"),
            order_type=data.get("type", "MARKET"),
            quantity=float(data.get("origQty", 0)),
            price=resolved_price,
            status=data.get("status", "NEW"),
            executed_quantity=float(data.get("executedQty", 0)),
            create_time=int(data.get("time", 0)),
            reduce_only=str(data.get("reduceOnly", "false")).lower() == "true",
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

    async def cancel_all_open_orders(self, symbol: str) -> dict[str, Any]:
        """Cancel all open orders for a symbol.

        Endpoint: DELETE /fapi/v1/allOpenOrders
        Used to clean up SL/TP bracket orders when closing a position.
        """
        return await self._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            params={"symbol": symbol},
            signed=True,
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
