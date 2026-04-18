"""Exchange reconciliation: compare local position state vs Binance on startup."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from src.core.event_log import EventLog
from src.notifications.telegram import AlertLevel, TelegramNotifier
from src.portfolio.manager import PortfolioManager
from src.utils.logger import get_logger


class DivergencePolicy(Enum):
    ALERT = "alert"
    BLOCK = "block"
    AUTO_FIX = "auto_fix"


@dataclass
class ReconciliationConfig:
    enabled: bool = True
    on_divergence: DivergencePolicy = DivergencePolicy.ALERT
    quantity_tolerance_pct: float = 1.0
    periodic_interval_seconds: int = 0  # 0 = startup only
    dust_threshold_usdt: float = 1.0


@dataclass
class Divergence:
    symbol: str
    market: str  # "spot" or "futures"
    divergence_type: str  # phantom_db, untracked_exchange, quantity_mismatch, side_mismatch
    db_state: dict[str, Any]
    exchange_state: dict[str, Any]
    severity: str  # "warning" or "critical"
    message: str


@dataclass
class ReconciliationResult:
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    agent_id: str = "default"
    market: str = "spot"
    divergences: list[Divergence] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.divergences) == 0


def _base_asset(symbol: str) -> str:
    """Extract base asset from a USDT-quoted symbol, e.g. BTCUSDT -> BTC."""
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


class ExchangeReconciler:
    """Compare local DB position state against exchange and detect divergence."""

    def __init__(
        self,
        portfolio_manager: PortfolioManager,
        spot_client: Any | None,
        futures_client: Any | None,
        spot_symbols: list[str],
        futures_symbols: list[str],
        notifier: TelegramNotifier,
        event_log: EventLog | None,
        risk_manager: Any,
        config: ReconciliationConfig,
        agent_id: str = "default",
    ) -> None:
        self._portfolio = portfolio_manager
        self._spot_client = spot_client
        self._futures_client = futures_client
        self._spot_symbols = spot_symbols
        self._futures_symbols = futures_symbols
        self._notifier = notifier
        self._event_log = event_log
        self._risk_manager = risk_manager
        self._config = config
        self._agent_id = agent_id
        self._logger = get_logger("ExchangeReconciler")

    async def reconcile_all(self) -> list[ReconciliationResult]:
        results: list[ReconciliationResult] = []

        if self._spot_client and self._spot_symbols:
            try:
                results.append(await self.reconcile_spot())
            except Exception:
                self._logger.exception("Spot reconciliation failed")

        if self._futures_client and self._futures_symbols:
            try:
                results.append(await self.reconcile_futures())
            except Exception:
                self._logger.exception("Futures reconciliation failed")

        return results

    async def reconcile_spot(self) -> ReconciliationResult:
        result = ReconciliationResult(agent_id=self._agent_id, market="spot")
        balances = await self._spot_client.get_all_balances()

        tracked_assets: set[str] = set()

        for symbol in self._spot_symbols:
            asset = _base_asset(symbol)
            tracked_assets.add(asset)
            db_pos = self._portfolio.get_position(symbol, market="spot")
            exchange_qty = balances.get(asset, 0.0)

            if db_pos and exchange_qty <= 0:
                result.divergences.append(
                    Divergence(
                        symbol=symbol,
                        market="spot",
                        divergence_type="phantom_db",
                        db_state={"quantity": db_pos.quantity, "entry_price": db_pos.entry_price},
                        exchange_state={"quantity": 0.0},
                        severity="critical",
                        message=(
                            f"DB has open position for {symbol} "
                            f"(qty={db_pos.quantity:.6f}) but exchange balance is zero"
                        ),
                    )
                )
            elif not db_pos and exchange_qty > 0:
                # Only flag if above dust threshold — estimate USDT value roughly
                # We don't have price here, so use raw quantity check against a generous threshold
                if exchange_qty > self._config.dust_threshold_usdt:
                    result.divergences.append(
                        Divergence(
                            symbol=symbol,
                            market="spot",
                            divergence_type="untracked_exchange",
                            db_state={"quantity": 0.0},
                            exchange_state={"quantity": exchange_qty},
                            severity="warning",
                            message=(
                                f"Exchange has {exchange_qty:.6f} {asset} "
                                f"but no open DB position for {symbol}"
                            ),
                        )
                    )
            elif db_pos and exchange_qty > 0:
                # Both exist — check quantity tolerance
                qty_diff = abs(db_pos.quantity - exchange_qty)
                pct_diff = qty_diff / db_pos.quantity * 100
                usdt_diff = qty_diff * db_pos.entry_price
                # Require BOTH pct over tolerance AND USDT value above dust
                # so pre-existing small balances (dust on the account from
                # before the agent started trading) don't alert forever.
                if (
                    pct_diff > self._config.quantity_tolerance_pct
                    and usdt_diff > self._config.dust_threshold_usdt
                ):
                    result.divergences.append(
                        Divergence(
                            symbol=symbol,
                            market="spot",
                            divergence_type="quantity_mismatch",
                            db_state={"quantity": db_pos.quantity},
                            exchange_state={"quantity": exchange_qty},
                            severity="warning",
                            message=(
                                f"{symbol} quantity mismatch: "
                                f"DB={db_pos.quantity:.6f} vs exchange={exchange_qty:.6f} "
                                f"({pct_diff:.1f}% diff, ~${usdt_diff:.2f})"
                            ),
                        )
                    )

        self._logger.info(
            "Spot reconciliation: %d symbols checked, %d divergences",
            len(self._spot_symbols),
            len(result.divergences),
        )
        return result

    async def reconcile_futures(self) -> ReconciliationResult:
        result = ReconciliationResult(agent_id=self._agent_id, market="futures")

        for symbol in self._futures_symbols:
            exchange_positions = await self._futures_client.get_position_risk(symbol)
            db_pos = self._portfolio.get_position(symbol, market="futures")

            has_exchange_pos = len(exchange_positions) > 0
            exchange_pos = exchange_positions[0] if has_exchange_pos else None

            if db_pos and not has_exchange_pos:
                result.divergences.append(
                    Divergence(
                        symbol=symbol,
                        market="futures",
                        divergence_type="phantom_db",
                        db_state={
                            "quantity": db_pos.quantity,
                            "side": db_pos.position_side,
                            "entry_price": db_pos.entry_price,
                        },
                        exchange_state={"quantity": 0.0},
                        severity="critical",
                        message=(
                            f"DB has open futures position for {symbol} "
                            f"({db_pos.position_side} qty={db_pos.quantity:.6f}) "
                            f"but exchange has no position"
                        ),
                    )
                )
            elif not db_pos and has_exchange_pos:
                result.divergences.append(
                    Divergence(
                        symbol=symbol,
                        market="futures",
                        divergence_type="untracked_exchange",
                        db_state={"quantity": 0.0},
                        exchange_state={
                            "quantity": abs(exchange_pos.position_amt),
                            "side": exchange_pos.position_side,
                            "entry_price": exchange_pos.entry_price,
                        },
                        severity="critical",
                        message=(
                            f"Exchange has futures position for {symbol} "
                            f"({exchange_pos.position_side} "
                            f"qty={abs(exchange_pos.position_amt):.6f}) "
                            f"but no open DB position"
                        ),
                    )
                )
            elif db_pos and has_exchange_pos:
                # Check side mismatch
                exchange_side = "LONG" if exchange_pos.position_amt > 0 else "SHORT"
                if db_pos.position_side and db_pos.position_side != exchange_side:
                    result.divergences.append(
                        Divergence(
                            symbol=symbol,
                            market="futures",
                            divergence_type="side_mismatch",
                            db_state={"side": db_pos.position_side},
                            exchange_state={"side": exchange_side},
                            severity="critical",
                            message=(
                                f"{symbol} futures side mismatch: "
                                f"DB={db_pos.position_side} vs exchange={exchange_side}"
                            ),
                        )
                    )

                # Check quantity tolerance
                exchange_qty = abs(exchange_pos.position_amt)
                qty_diff = abs(db_pos.quantity - exchange_qty)
                pct_diff = qty_diff / db_pos.quantity * 100
                usdt_diff = qty_diff * db_pos.entry_price
                if (
                    pct_diff > self._config.quantity_tolerance_pct
                    and usdt_diff > self._config.dust_threshold_usdt
                ):
                    result.divergences.append(
                        Divergence(
                            symbol=symbol,
                            market="futures",
                            divergence_type="quantity_mismatch",
                            db_state={"quantity": db_pos.quantity},
                            exchange_state={"quantity": exchange_qty},
                            severity="warning",
                            message=(
                                f"{symbol} futures quantity mismatch: "
                                f"DB={db_pos.quantity:.6f} vs "
                                f"exchange={exchange_qty:.6f} "
                                f"({pct_diff:.1f}% diff, ~${usdt_diff:.2f})"
                            ),
                        )
                    )

        self._logger.info(
            "Futures reconciliation: %d symbols checked, %d divergences",
            len(self._futures_symbols),
            len(result.divergences),
        )
        return result

    async def handle_divergences(self, results: list[ReconciliationResult]) -> None:
        all_divs = [d for r in results for d in r.divergences]
        if not all_divs:
            self._logger.info("Reconciliation clean — no divergences detected")
            return

        # Log all divergences
        for div in all_divs:
            log_fn = self._logger.error if div.severity == "critical" else self._logger.warning
            log_fn("RECONCILIATION %s: %s", div.divergence_type.upper(), div.message)

        # Record in event log
        if self._event_log:
            await self._event_log.log(
                "reconciliation_divergence",
                {
                    "divergence_count": len(all_divs),
                    "critical_count": sum(1 for d in all_divs if d.severity == "critical"),
                    "details": [
                        {
                            "symbol": d.symbol,
                            "market": d.market,
                            "type": d.divergence_type,
                            "severity": d.severity,
                            "message": d.message,
                        }
                        for d in all_divs
                    ],
                },
            )

        # Send Telegram alert
        critical = [d for d in all_divs if d.severity == "critical"]
        warnings = [d for d in all_divs if d.severity == "warning"]
        lines = [f"<b>Reconciliation Alert ({self._agent_id})</b>"]
        if critical:
            lines.append(f"\n<b>{len(critical)} CRITICAL:</b>")
            for d in critical:
                lines.append(f"  - {d.message}")
        if warnings:
            lines.append(f"\n{len(warnings)} warnings:")
            for d in warnings:
                lines.append(f"  - {d.message}")

        policy = self._config.on_divergence
        lines.append(f"\nPolicy: <code>{policy.value}</code>")
        if policy == DivergencePolicy.BLOCK:
            lines.append("Trading BLOCKED until manual resolution.")

        await self._notifier.send_alert(
            "\n".join(lines),
            level=AlertLevel.CRITICAL if critical else AlertLevel.WARNING,
        )

        # Apply policy
        if policy == DivergencePolicy.BLOCK and critical:
            self._risk_manager.set_reconciliation_block(
                f"Reconciliation found {len(critical)} critical divergences"
            )

    async def run_periodic(self) -> None:
        """Periodic reconciliation loop. Cancel via task cancellation."""
        interval = self._config.periodic_interval_seconds
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                results = await self.reconcile_all()
                await self.handle_divergences(results)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Periodic reconciliation failed")
