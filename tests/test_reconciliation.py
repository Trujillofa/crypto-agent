"""Tests for exchange reconciliation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.reconciliation import (
    DivergencePolicy,
    ExchangeReconciler,
    ReconciliationConfig,
    _base_asset,
)
from src.portfolio.models import Position, PositionStatus


def _make_reconciler(
    *,
    spot_client: MagicMock | None = None,
    futures_client: MagicMock | None = None,
    spot_symbols: list[str] | None = None,
    futures_symbols: list[str] | None = None,
    positions: dict[tuple[str, str], Position] | None = None,
    policy: DivergencePolicy = DivergencePolicy.ALERT,
    quantity_tolerance_pct: float = 1.0,
    dust_threshold_usdt: float = 1.0,
) -> ExchangeReconciler:
    portfolio = MagicMock()
    portfolio.get_position = MagicMock(
        side_effect=lambda symbol, market="spot": (positions or {}).get((symbol, market))
    )

    config = ReconciliationConfig(
        enabled=True,
        on_divergence=policy,
        quantity_tolerance_pct=quantity_tolerance_pct,
        dust_threshold_usdt=dust_threshold_usdt,
    )

    return ExchangeReconciler(
        portfolio_manager=portfolio,
        spot_client=spot_client,
        futures_client=futures_client,
        spot_symbols=spot_symbols or [],
        futures_symbols=futures_symbols or [],
        notifier=MagicMock(),
        event_log=None,
        risk_manager=MagicMock(),
        config=config,
        agent_id="test",
    )


def _make_position(
    symbol: str = "BTCUSDT",
    market: str = "spot",
    quantity: float = 0.5,
    entry_price: float = 50000.0,
    position_side: str | None = None,
) -> Position:
    return Position(
        id=1,
        symbol=symbol,
        market=market,
        quantity=quantity,
        entry_price=entry_price,
        status=PositionStatus.OPEN,
        position_side=position_side,
    )


class TestBaseAsset:
    def test_btcusdt(self) -> None:
        assert _base_asset("BTCUSDT") == "BTC"

    def test_solusdt(self) -> None:
        assert _base_asset("SOLUSDT") == "SOL"

    def test_non_usdt(self) -> None:
        assert _base_asset("BTCETH") == "BTCETH"


class TestSpotReconciliation:
    @pytest.mark.asyncio
    async def test_clean_no_positions(self) -> None:
        """No DB positions, no exchange balances -> clean."""
        client = AsyncMock()
        client.get_all_balances = AsyncMock(return_value={"USDT": 1000.0})

        recon = _make_reconciler(
            spot_client=client,
            spot_symbols=["BTCUSDT", "SOLUSDT"],
        )
        result = await recon.reconcile_spot()
        assert result.is_clean

    @pytest.mark.asyncio
    async def test_clean_matching_positions(self) -> None:
        """DB position matches exchange balance."""
        client = AsyncMock()
        client.get_all_balances = AsyncMock(return_value={"BTC": 0.5, "USDT": 1000.0})

        pos = _make_position(symbol="BTCUSDT", quantity=0.5)
        recon = _make_reconciler(
            spot_client=client,
            spot_symbols=["BTCUSDT"],
            positions={("BTCUSDT", "spot"): pos},
        )
        result = await recon.reconcile_spot()
        assert result.is_clean

    @pytest.mark.asyncio
    async def test_phantom_db_position(self) -> None:
        """DB has position but exchange has zero balance -> critical phantom."""
        client = AsyncMock()
        client.get_all_balances = AsyncMock(return_value={"USDT": 1000.0})

        pos = _make_position(symbol="BTCUSDT", quantity=0.5)
        recon = _make_reconciler(
            spot_client=client,
            spot_symbols=["BTCUSDT"],
            positions={("BTCUSDT", "spot"): pos},
        )
        result = await recon.reconcile_spot()
        assert not result.is_clean
        assert len(result.divergences) == 1
        div = result.divergences[0]
        assert div.divergence_type == "phantom_db"
        assert div.severity == "critical"

    @pytest.mark.asyncio
    async def test_untracked_exchange_balance(self) -> None:
        """Exchange has balance but no DB position -> warning."""
        client = AsyncMock()
        client.get_all_balances = AsyncMock(return_value={"BTC": 1.5, "USDT": 1000.0})

        recon = _make_reconciler(
            spot_client=client,
            spot_symbols=["BTCUSDT"],
        )
        result = await recon.reconcile_spot()
        assert not result.is_clean
        assert result.divergences[0].divergence_type == "untracked_exchange"
        assert result.divergences[0].severity == "warning"

    @pytest.mark.asyncio
    async def test_untracked_below_dust_threshold(self) -> None:
        """Exchange balance below dust threshold -> ignored."""
        client = AsyncMock()
        client.get_all_balances = AsyncMock(return_value={"BTC": 0.00001, "USDT": 1000.0})

        recon = _make_reconciler(
            spot_client=client,
            spot_symbols=["BTCUSDT"],
            dust_threshold_usdt=1.0,
        )
        result = await recon.reconcile_spot()
        assert result.is_clean

    @pytest.mark.asyncio
    async def test_quantity_mismatch(self) -> None:
        """DB and exchange both exist but quantities differ beyond tolerance."""
        client = AsyncMock()
        client.get_all_balances = AsyncMock(return_value={"BTC": 0.4, "USDT": 1000.0})

        pos = _make_position(symbol="BTCUSDT", quantity=0.5)
        recon = _make_reconciler(
            spot_client=client,
            spot_symbols=["BTCUSDT"],
            positions={("BTCUSDT", "spot"): pos},
            quantity_tolerance_pct=1.0,
        )
        result = await recon.reconcile_spot()
        assert not result.is_clean
        assert result.divergences[0].divergence_type == "quantity_mismatch"

    @pytest.mark.asyncio
    async def test_quantity_within_tolerance(self) -> None:
        """Quantities differ but within tolerance -> clean."""
        client = AsyncMock()
        client.get_all_balances = AsyncMock(return_value={"BTC": 0.4995, "USDT": 1000.0})

        pos = _make_position(symbol="BTCUSDT", quantity=0.5)
        recon = _make_reconciler(
            spot_client=client,
            spot_symbols=["BTCUSDT"],
            positions={("BTCUSDT", "spot"): pos},
            quantity_tolerance_pct=1.0,
        )
        result = await recon.reconcile_spot()
        assert result.is_clean


class TestFuturesReconciliation:
    @pytest.mark.asyncio
    async def test_clean_no_positions(self) -> None:
        client = AsyncMock()
        client.get_position_risk = AsyncMock(return_value=[])

        recon = _make_reconciler(
            futures_client=client,
            futures_symbols=["BTCUSDT"],
        )
        result = await recon.reconcile_futures()
        assert result.is_clean

    @pytest.mark.asyncio
    async def test_phantom_db_futures_position(self) -> None:
        client = AsyncMock()
        client.get_position_risk = AsyncMock(return_value=[])

        pos = _make_position(
            symbol="BTCUSDT", market="futures", quantity=0.1, position_side="LONG"
        )
        recon = _make_reconciler(
            futures_client=client,
            futures_symbols=["BTCUSDT"],
            positions={("BTCUSDT", "futures"): pos},
        )
        result = await recon.reconcile_futures()
        assert not result.is_clean
        assert result.divergences[0].divergence_type == "phantom_db"
        assert result.divergences[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_untracked_exchange_futures(self) -> None:
        exchange_pos = MagicMock()
        exchange_pos.position_amt = 0.1
        exchange_pos.position_side = "LONG"
        exchange_pos.entry_price = 50000.0

        client = AsyncMock()
        client.get_position_risk = AsyncMock(return_value=[exchange_pos])

        recon = _make_reconciler(
            futures_client=client,
            futures_symbols=["BTCUSDT"],
        )
        result = await recon.reconcile_futures()
        assert not result.is_clean
        assert result.divergences[0].divergence_type == "untracked_exchange"
        assert result.divergences[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_side_mismatch(self) -> None:
        exchange_pos = MagicMock()
        exchange_pos.position_amt = -0.1  # SHORT on exchange
        exchange_pos.position_side = "SHORT"
        exchange_pos.entry_price = 50000.0

        client = AsyncMock()
        client.get_position_risk = AsyncMock(return_value=[exchange_pos])

        pos = _make_position(
            symbol="BTCUSDT", market="futures", quantity=0.1, position_side="LONG"
        )
        recon = _make_reconciler(
            futures_client=client,
            futures_symbols=["BTCUSDT"],
            positions={("BTCUSDT", "futures"): pos},
        )
        result = await recon.reconcile_futures()
        side_divs = [d for d in result.divergences if d.divergence_type == "side_mismatch"]
        assert len(side_divs) == 1
        assert side_divs[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_futures_quantity_mismatch(self) -> None:
        exchange_pos = MagicMock()
        exchange_pos.position_amt = 0.2  # LONG
        exchange_pos.position_side = "LONG"
        exchange_pos.entry_price = 50000.0

        client = AsyncMock()
        client.get_position_risk = AsyncMock(return_value=[exchange_pos])

        pos = _make_position(
            symbol="BTCUSDT", market="futures", quantity=0.1, position_side="LONG"
        )
        recon = _make_reconciler(
            futures_client=client,
            futures_symbols=["BTCUSDT"],
            positions={("BTCUSDT", "futures"): pos},
        )
        result = await recon.reconcile_futures()
        qty_divs = [d for d in result.divergences if d.divergence_type == "quantity_mismatch"]
        assert len(qty_divs) == 1


class TestDivergenceHandling:
    @pytest.mark.asyncio
    async def test_clean_logs_no_divergences(self) -> None:
        """Clean results should not trigger alerts."""
        recon = _make_reconciler()
        result = MagicMock()
        result.divergences = []
        await recon.handle_divergences([result])
        recon._notifier.send_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_policy_sends_telegram(self) -> None:
        """Alert policy sends notification but doesn't block trading."""
        recon = _make_reconciler(policy=DivergencePolicy.ALERT)
        recon._notifier.send_alert = AsyncMock(return_value=True)

        from src.execution.reconciliation import Divergence, ReconciliationResult

        result = ReconciliationResult(
            agent_id="test",
            market="spot",
            divergences=[
                Divergence(
                    symbol="BTCUSDT",
                    market="spot",
                    divergence_type="phantom_db",
                    db_state={"quantity": 0.5},
                    exchange_state={"quantity": 0.0},
                    severity="critical",
                    message="test divergence",
                )
            ],
        )
        await recon.handle_divergences([result])
        recon._notifier.send_alert.assert_called_once()
        recon._risk_manager.set_reconciliation_block.assert_not_called()

    @pytest.mark.asyncio
    async def test_block_policy_blocks_trading(self) -> None:
        """Block policy sends alert AND blocks trading."""
        recon = _make_reconciler(policy=DivergencePolicy.BLOCK)
        recon._notifier.send_alert = AsyncMock(return_value=True)

        from src.execution.reconciliation import Divergence, ReconciliationResult

        result = ReconciliationResult(
            agent_id="test",
            market="futures",
            divergences=[
                Divergence(
                    symbol="BTCUSDT",
                    market="futures",
                    divergence_type="phantom_db",
                    db_state={"quantity": 0.1},
                    exchange_state={"quantity": 0.0},
                    severity="critical",
                    message="test divergence",
                )
            ],
        )
        await recon.handle_divergences([result])
        recon._risk_manager.set_reconciliation_block.assert_called_once()

    @pytest.mark.asyncio
    async def test_block_policy_ignores_warnings_only(self) -> None:
        """Block policy doesn't block on warnings (only critical)."""
        recon = _make_reconciler(policy=DivergencePolicy.BLOCK)
        recon._notifier.send_alert = AsyncMock(return_value=True)

        from src.execution.reconciliation import Divergence, ReconciliationResult

        result = ReconciliationResult(
            agent_id="test",
            market="spot",
            divergences=[
                Divergence(
                    symbol="BTCUSDT",
                    market="spot",
                    divergence_type="quantity_mismatch",
                    db_state={"quantity": 0.5},
                    exchange_state={"quantity": 0.4},
                    severity="warning",
                    message="test warning",
                )
            ],
        )
        await recon.handle_divergences([result])
        recon._risk_manager.set_reconciliation_block.assert_not_called()


class TestReconcileAll:
    @pytest.mark.asyncio
    async def test_runs_both_spot_and_futures(self) -> None:
        spot_client = AsyncMock()
        spot_client.get_all_balances = AsyncMock(return_value={"USDT": 1000.0})

        futures_client = AsyncMock()
        futures_client.get_position_risk = AsyncMock(return_value=[])

        recon = _make_reconciler(
            spot_client=spot_client,
            futures_client=futures_client,
            spot_symbols=["BTCUSDT"],
            futures_symbols=["BTCUSDT"],
        )
        results = await recon.reconcile_all()
        assert len(results) == 2
        assert results[0].market == "spot"
        assert results[1].market == "futures"

    @pytest.mark.asyncio
    async def test_skips_if_no_client(self) -> None:
        recon = _make_reconciler(spot_symbols=["BTCUSDT"], futures_symbols=["BTCUSDT"])
        results = await recon.reconcile_all()
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_continues_on_spot_failure(self) -> None:
        spot_client = AsyncMock()
        spot_client.get_all_balances = AsyncMock(side_effect=RuntimeError("API down"))

        futures_client = AsyncMock()
        futures_client.get_position_risk = AsyncMock(return_value=[])

        recon = _make_reconciler(
            spot_client=spot_client,
            futures_client=futures_client,
            spot_symbols=["BTCUSDT"],
            futures_symbols=["BTCUSDT"],
        )
        results = await recon.reconcile_all()
        # Spot failed but futures succeeded
        assert len(results) == 1
        assert results[0].market == "futures"
