import asyncio

import pytest

from src.execution import TradingConfig
from src.main import (
    AISettings,
    Settings,
    StrategySettings,
    _cancel_background_tasks,
    _resolve_overseer_launch,
    _resolve_signal_routing,
)
from src.notifications.telegram import TelegramConfig
from src.strategy.signals import Signal, SignalType


@pytest.mark.asyncio
async def test_cancel_background_tasks_skips_none() -> None:
    async def never_finishes() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(never_finishes())

    await _cancel_background_tasks(None, task, None)

    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_background_tasks_handles_completed_task() -> None:
    async def finishes_immediately() -> str:
        return "done"

    task = asyncio.create_task(finishes_immediately())
    await task

    await _cancel_background_tasks(task)

    assert task.done()
    assert task.result() == "done"


def _make_signal(trading_mode: str = "spot") -> Signal:
    return Signal(
        type=SignalType.BUY,
        symbol="BTCUSDT",
        price=50000.0,
        confidence=0.8,
        reason="test",
        indicators={"atr_14": 250.0},
        trading_mode=trading_mode,
    )


def test_resolve_signal_routing_reroutes_to_futures_for_futures_default() -> None:
    signal = _make_signal("spot")

    routed, should_mirror = _resolve_signal_routing(signal, {"BTCUSDT"}, "futures")

    assert should_mirror is False
    assert routed.trading_mode == "futures"
    assert routed.symbol == signal.symbol
    assert routed.price == signal.price


def test_resolve_signal_routing_preserves_spot_and_mirrors_for_spot_default() -> None:
    signal = _make_signal("spot")

    routed, should_mirror = _resolve_signal_routing(signal, {"BTCUSDT"}, "spot")

    assert should_mirror is True
    assert routed is signal


def test_resolve_signal_routing_preserves_explicit_futures_signal() -> None:
    signal = _make_signal("futures")

    routed, should_mirror = _resolve_signal_routing(signal, {"BTCUSDT"}, "futures")

    assert should_mirror is False
    assert routed is signal


def _make_settings(
    *, ai_enabled: bool = True, telegram_enabled: bool = False, bot_token: str = ""
) -> Settings:
    return Settings(
        agent_id="test-agent",
        mode="paper",
        log_level="INFO",
        trading_pairs=["BTCUSDT"],
        timeframe="1h",
        database={"host": "timescaledb", "port": 5432, "name": "marketdata", "user": "trading"},
        prometheus_port=8000,
        trading_execution=TradingConfig(
            api_key="",
            api_secret="",
            test_mode=True,
            enabled=True,
            symbols=["BTCUSDT"],
            order_size_usdt=100.0,
            stop_loss_pct=0.02,
            take_profit_pct=0.05,
            sl_atr_multiplier=2.0,
            tp_atr_multiplier=4.5,
            trailing_activate_atr=1.5,
            trailing_offset_atr=1.0,
            use_atr_sizing=False,
            atr_multiplier=1.0,
            risk_per_trade_pct=0.02,
        ),
        strategy=StrategySettings(evaluation_interval_seconds=60),
        telegram=TelegramConfig(
            bot_token=bot_token,
            chat_id="",
            enabled=telegram_enabled,
            rate_limit_seconds=5,
            allowed_updates=("message",),
        ),
        ai=AISettings(
            enabled=ai_enabled,
            provider="xai",
            model="grok-4-1-fast-reasoning",
            polling_interval=60.0,
            max_history=10,
            allowed_chat_ids=[],
            api_key="test-key",
        ),
        use_websocket=True,
    )


def test_resolve_overseer_launch_skips_overseer_when_telegram_disabled() -> None:
    settings = _make_settings(telegram_enabled=False)

    enabled, warning = _resolve_overseer_launch(settings)

    assert enabled is False
    assert warning is None


def test_resolve_overseer_launch_warns_when_telegram_enabled_but_missing_token() -> None:
    settings = _make_settings(telegram_enabled=True, bot_token="")

    enabled, warning = _resolve_overseer_launch(settings)

    assert enabled is False
    assert warning == "AI overseer enabled but TELEGRAM_BOT_TOKEN missing; overseer disabled"


def test_resolve_overseer_launch_enables_with_telegram_and_token() -> None:
    settings = _make_settings(telegram_enabled=True, bot_token="token")

    enabled, warning = _resolve_overseer_launch(settings)

    assert enabled is True
    assert warning is None
