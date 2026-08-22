"""Adversarial coverage for backtest cost book, causality, split, and live-go."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.backtest.artifacts import create_manifest
from src.backtest.engine import BacktestConfig, BacktestEngine
from src.backtest.factory import BacktestRequest, build_backtest_config
from src.backtest.metrics import calculate_backtest_metrics
from src.backtest.ranking import RankedCandidate, rank_by_selection_score
from src.backtest.research_safety import (
    LiveGoRefused,
    refuse_broken_param_sweep,
    refuse_live_go,
)
from src.backtest.timeframes import periods_per_year
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class BuyOnClose100(BaseStrategy):
    def get_name(self) -> str:
        return "BuyOnClose100"

    async def evaluate(self, symbol: str, indicators: dict[str, object]) -> Signal:
        price = float(indicators["close_price"])
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "buy", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "hold", indicators)


class PeekIfFutureLeaked(BaseStrategy):
    def get_name(self) -> str:
        return "PeekIfFutureLeaked"

    async def evaluate(self, symbol: str, indicators: dict[str, object]) -> Signal:
        leaked = any(
            key in indicators
            for key in ("next_close", "_lookahead_close", "future_bars", "future_close")
        )
        if leaked:
            return Signal(
                SignalType.BUY, symbol, float(indicators["close_price"]), 1.0, "peek", indicators
            )
        return Signal(
            SignalType.HOLD, symbol, float(indicators["close_price"]), 0.0, "hold", indicators
        )


def _reader(rows: list[dict[str, object]]) -> IndicatorReader:
    reader = IndicatorReader({})

    async def fetch_range(*_args: object) -> list[dict[str, object]]:
        return rows

    reader.fetch_range = fetch_range  # type: ignore[method-assign]
    return reader


def _settings() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        trading_execution=SimpleNamespace(
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
            use_atr_sizing=False,
            atr_multiplier=1.5,
            risk_per_trade_pct=0.02,
        ),
        futures=SimpleNamespace(enabled=False, symbols=[], default_leverage=5),
    )


def test_rank_by_selection_ignores_swapped_holdout() -> None:
    first = [
        RankedCandidate("alpha", selection_score=2.0, holdout_score=0.0),
        RankedCandidate("beta", selection_score=1.0, holdout_score=99.0),
    ]
    swapped = [
        RankedCandidate("alpha", selection_score=2.0, holdout_score=99.0),
        RankedCandidate("beta", selection_score=1.0, holdout_score=0.0),
    ]
    assert [item.name for item in rank_by_selection_score(first)] == ["alpha", "beta"]
    assert [item.name for item in rank_by_selection_score(swapped)] == ["alpha", "beta"]


def test_backtest_config_refuses_cost_mutation() -> None:
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2024-01-01",
        end_date="2024-01-02",
        fee_rate=0.0004,
    )
    with pytest.raises(FrozenInstanceError):
        config.fee_rate = 0.0  # type: ignore[misc]


@pytest.mark.asyncio
async def test_engine_cost_book_ignores_forced_config_mutation() -> None:
    rows = [
        {"time": "2024-01-01T00:00:00", "open_price": 99.0, "close_price": 100.0},
        {"time": "2024-01-01T01:00:00", "open_price": 105.0, "close_price": 106.0},
        {"time": "2024-01-01T02:00:00", "open_price": 110.0, "close_price": 111.0},
    ]
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2024-01-01",
        end_date="2024-01-02",
        fee_rate=0.0,
        slippage_pct=0.10,
        apply_global_trend_filter=False,
        execution_profile="execution_parity_v2",
        strategy_classes=[BuyOnClose100],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5, "sell_threshold": -0.5},
    )
    engine = BacktestEngine(config, _reader(rows))
    object.__setattr__(config, "slippage_pct", 0.0)
    result = await engine.run()

    assert result.total_trades == 1
    assert result.trades[0].entry_price == pytest.approx(105.0 * 1.10)


@pytest.mark.asyncio
async def test_v2_does_not_fill_at_signal_bar_close() -> None:
    rows = [
        {"time": "2024-01-01T00:00:00", "open_price": 99.0, "close_price": 100.0},
        {"time": "2024-01-01T01:00:00", "open_price": 90.0, "close_price": 200.0},
    ]
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2024-01-01",
        end_date="2024-01-02",
        fee_rate=0.0,
        slippage_pct=0.0,
        apply_global_trend_filter=False,
        execution_profile="execution_parity_v2",
        strategy_classes=[BuyOnClose100],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5, "sell_threshold": -0.5},
    )
    result = await BacktestEngine(config, _reader(rows)).run()

    assert result.total_trades == 1
    assert result.trades[0].signal_time == "2024-01-01T00:00:00"
    assert result.trades[0].entry_price == pytest.approx(90.0)
    assert result.trades[0].fill_source == "next_bar_open"


@pytest.mark.asyncio
async def test_engine_does_not_leak_future_bar_into_evaluate() -> None:
    rows = [
        {"time": "2024-01-01T00:00:00", "open_price": 99.0, "close_price": 100.0},
        {"time": "2024-01-01T01:00:00", "open_price": 150.0, "close_price": 200.0},
    ]
    peek = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2024-01-01",
        end_date="2024-01-02",
        fee_rate=0.0,
        slippage_pct=0.0,
        apply_global_trend_filter=False,
        execution_profile="execution_parity_v2",
        strategy_classes=[PeekIfFutureLeaked],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5, "sell_threshold": -0.5},
    )
    peek_result = await BacktestEngine(peek, _reader(rows)).run()
    assert peek_result.total_trades == 0


def test_unknown_timeframe_is_refused() -> None:
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        periods_per_year("97m")
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="97m",
        start_date="2024-01-01",
        end_date="2024-01-02",
    )
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        calculate_backtest_metrics(
            config=config,
            equity_curve=[100.0, 101.0],
            trades=[],
            blocked_buy_count=0,
            basis_blocked_buy_count=0,
            dislocation_blocked_buy_count=0,
        )


def test_refuse_live_go_and_broken_sweep() -> None:
    refuse_live_go(argv=["--symbol", "SOLUSDT"], flags={"live_go": False})
    with pytest.raises(LiveGoRefused, match="not a live-go"):
        refuse_live_go(argv=["--symbol", "SOLUSDT", "--live"])
    with pytest.raises(LiveGoRefused, match="not a live-go"):
        refuse_live_go(flags={"live_go": True})
    with pytest.raises(LiveGoRefused, match="not a live-go"):
        refuse_live_go(flags={"promote": True})
    with pytest.raises(LiveGoRefused, match="not a live-go"):
        refuse_live_go(env={"CRYPTO_AGENT_LIVE_GO": "true"})
    with pytest.raises(RuntimeError, match="not a selection tool"):
        refuse_broken_param_sweep()


def test_canonical_research_scripts_cannot_place_live_orders() -> None:
    roots = [
        Path("scripts/run_backtest.py"),
        Path("scripts/experiment_autopilot.py"),
        Path("scripts/run_wfo.py"),
        Path("scripts/run_wfo_sweep.py"),
        Path("scripts/run_config_search.py"),
        Path("scripts/run_mtf_search.py"),
        Path("src/backtest/engine.py"),
    ]
    forbidden = ("place_order", "BinanceClient", "BinanceFuturesClient", "--live")
    for path in roots:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path} must not contain {token}"


def test_factory_uses_realistic_fee_when_override_omitted() -> None:
    config = build_backtest_config(
        request=BacktestRequest(
            symbol="SOLUSDT",
            timeframe="1h",
            start="2024-01-01",
            end="2024-02-01",
        ),
        settings=_settings(),
        raw_config={},
        strategy_classes=[],
        strategy_configs=[],
        aggregator_config={},
    )
    assert config.fee_rate == 0.0004
    assert config.slippage_pct == 0.0002


def test_manifest_omits_trade_dump() -> None:
    from src.backtest.models import BacktestResult, Trade

    result = BacktestResult(
        total_return=1.0,
        total_return_pct=0.1,
        max_drawdown=0.0,
        win_rate=100.0,
        total_trades=1,
        trades=[
            Trade(
                entry_time="2024-01-01",
                exit_time="2024-01-02",
                side="BUY",
                entry_price=1.0,
                exit_price=2.0,
                quantity=1.0,
                pnl=1.0,
                return_pct=100.0,
            )
        ],
        final_equity=10_001.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        profit_factor=1.0,
        avg_win_loss_ratio=1.0,
    )
    manifest = create_manifest(
        config=BacktestConfig(
            symbol="SOLUSDT",
            timeframe="1h",
            start_date="2024-01-01",
            end_date="2024-01-02",
        ),
        result=result,
    )
    assert "trades" not in manifest.result
    assert manifest.result["total_trades"] == 1
