from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.utils.logger import get_logger


@dataclass
class DelistingEntry:
    """A symbol on the delisting watchlist."""

    symbol: str
    reason: str
    added_at: float = field(default_factory=time.time)
    auto_exit: bool = True  # If True, auto-exit position on next signal cycle


class SafetyGuard:
    """Safety infrastructure for 2026 crypto trading.

    Handles:
    1. API Kill Switch: Auto-pause trading on 403/429 HTTP errors with cooldown
    2. Delisting Watchlist: Track symbols facing regulatory risk, auto-exit positions
    3. Hard Stop Loss tracking: Bot-side stop loss independent of exchange orders
    """

    def __init__(
        self,
        api_cooldown_seconds: float = 60.0,
        max_api_errors_per_window: int = 3,
        api_error_window_seconds: float = 300.0,
    ) -> None:
        self._logger = get_logger(self.__class__.__name__)

        # API Kill Switch
        self._api_cooldown_seconds = api_cooldown_seconds
        self._max_api_errors = max_api_errors_per_window
        self._api_error_window = api_error_window_seconds
        self._api_errors: list[tuple[float, int]] = []  # (timestamp, http_status)
        self._api_paused_until: float = 0.0

        # Delisting Watchlist
        self._delisting_watchlist: dict[str, DelistingEntry] = {}

        # Hard Stop Losses (bot-side, independent of exchange)
        self._hard_stops: dict[str, float] = {}  # symbol -> stop price

    # --- API Kill Switch ---

    def record_http_error(self, status_code: int) -> bool:
        """Record an HTTP error and check if trading should be paused.

        Args:
            status_code: HTTP status code (403, 429, etc.)

        Returns:
            True if trading is now paused
        """
        now = time.time()
        self._api_errors.append((now, status_code))

        # Clean old errors outside the window
        cutoff = now - self._api_error_window
        self._api_errors = [(t, s) for t, s in self._api_errors if t >= cutoff]

        # Immediate pause on 403 (forbidden — likely API key revoked or IP banned)
        if status_code == 403:
            self._api_paused_until = now + self._api_cooldown_seconds * 5  # Long cooldown
            self._logger.error(
                "API 403 Forbidden — trading paused for %.0fs",
                self._api_cooldown_seconds * 5,
            )
            return True

        # Rate limit (429) — shorter cooldown
        if status_code == 429:
            self._api_paused_until = now + self._api_cooldown_seconds
            self._logger.warning(
                "API 429 Rate Limited — trading paused for %.0fs",
                self._api_cooldown_seconds,
            )
            return True

        # Too many errors in window → pause
        critical_errors = [s for _, s in self._api_errors if s in (403, 429, 500, 502, 503)]
        if len(critical_errors) >= self._max_api_errors:
            self._api_paused_until = now + self._api_cooldown_seconds
            self._logger.error(
                "%d API errors in %.0fs window — trading paused for %.0fs",
                len(critical_errors),
                self._api_error_window,
                self._api_cooldown_seconds,
            )
            return True

        return False

    def is_api_paused(self) -> tuple[bool, str]:
        """Check if API access is paused due to errors.

        Returns:
            (is_paused, reason) tuple
        """
        now = time.time()
        if now < self._api_paused_until:
            remaining = self._api_paused_until - now
            return True, f"API paused for {remaining:.0f}s after HTTP errors"
        return False, ""

    def clear_api_pause(self) -> None:
        """Manually clear the API pause (e.g., after human verification)."""
        self._api_paused_until = 0.0
        self._api_errors.clear()
        self._logger.info("API pause cleared manually")

    # --- Delisting Watchlist ---

    def add_to_watchlist(self, symbol: str, reason: str, auto_exit: bool = True) -> None:
        """Add a symbol to the delisting watchlist.

        Args:
            symbol: Trading pair symbol (e.g., "LUNAUSDT")
            reason: Reason for watchlisting (e.g., "MiCA regulatory freeze")
            auto_exit: If True, signal auto-exit on next evaluation cycle
        """
        self._delisting_watchlist[symbol] = DelistingEntry(
            symbol=symbol, reason=reason, auto_exit=auto_exit,
        )
        self._logger.warning(
            "Symbol %s added to delisting watchlist: %s (auto_exit=%s)",
            symbol, reason, auto_exit,
        )

    def remove_from_watchlist(self, symbol: str) -> None:
        """Remove a symbol from the delisting watchlist."""
        self._delisting_watchlist.pop(symbol, None)

    def is_watchlisted(self, symbol: str) -> tuple[bool, str]:
        """Check if a symbol is on the delisting watchlist.

        Returns:
            (is_watchlisted, reason) tuple
        """
        entry = self._delisting_watchlist.get(symbol)
        if entry is None:
            return False, ""
        return True, entry.reason

    def should_auto_exit(self, symbol: str) -> bool:
        """Check if a symbol should be auto-exited due to delisting risk."""
        entry = self._delisting_watchlist.get(symbol)
        return entry is not None and entry.auto_exit

    def get_watchlist(self) -> list[DelistingEntry]:
        """Get all symbols on the delisting watchlist."""
        return list(self._delisting_watchlist.values())

    # --- Hard Stop Losses ---

    def set_hard_stop(self, symbol: str, stop_price: float) -> None:
        """Set a bot-side hard stop loss for a symbol.

        This is independent of exchange stop orders. The bot checks this
        on every tick and exits immediately if triggered.
        """
        self._hard_stops[symbol] = stop_price
        self._logger.info("Hard stop set for %s at %.4f", symbol, stop_price)

    def clear_hard_stop(self, symbol: str) -> None:
        """Clear the hard stop for a symbol."""
        self._hard_stops.pop(symbol, None)

    def check_hard_stop(self, symbol: str, current_price: float) -> bool:
        """Check if a hard stop loss has been triggered.

        Args:
            symbol: Trading pair symbol
            current_price: Current market price

        Returns:
            True if the hard stop was triggered (should exit immediately)
        """
        stop_price = self._hard_stops.get(symbol)
        if stop_price is None:
            return False

        if current_price <= stop_price:
            self._logger.warning(
                "HARD STOP TRIGGERED for %s: price %.4f <= stop %.4f",
                symbol, current_price, stop_price,
            )
            self._hard_stops.pop(symbol, None)
            return True

        return False

    def get_hard_stop(self, symbol: str) -> float | None:
        """Get the current hard stop price for a symbol."""
        return self._hard_stops.get(symbol)
