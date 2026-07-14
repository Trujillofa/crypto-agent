"""Stable data contracts shared by the backtest simulator, reports, and artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from src.backtest.cost_overrides import (
    DEFAULT_FUTURES_FUNDING_RATE,
    REALISTIC_FEE_RATE,
    REALISTIC_SLIPPAGE_PCT,
    FundingCadence,
)
from src.strategy.base import BaseStrategy
from src.strategy.basis_premium_filter import BasisPremiumFilterConfig
from src.strategy.cross_venue_dislocation import CrossVenueDislocationConfig
from src.strategy.session_liquidity import SessionLiquidityRouterConfig

ExecutionProfile = Literal["legacy_v1", "execution_parity_v2"]


@dataclass
class BacktestConfig:
    """Configuration for a single historical simulator run."""

    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float = 10000.0
    fee_rate: float = REALISTIC_FEE_RATE
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    sl_atr_multiplier: float = 2.0
    tp_atr_multiplier: float = 4.5
    trailing_activate_atr: float = 1.5
    trailing_offset_atr: float = 1.0
    slippage_pct: float = REALISTIC_SLIPPAGE_PCT
    risk_per_trade: float = 0.02
    use_atr_sizing: bool = False
    atr_multiplier: float = 1.5
    apply_global_trend_filter: bool = True
    global_trend_filter_buffer_pct: float = 0.0
    global_trend_filter_source: str = "engine_default"
    config_global_trend_filter_enabled: bool | None = None
    session_liquidity_router: SessionLiquidityRouterConfig = field(
        default_factory=SessionLiquidityRouterConfig
    )
    basis_premium_filter: BasisPremiumFilterConfig = field(default_factory=BasisPremiumFilterConfig)
    cross_venue_dislocation: CrossVenueDislocationConfig = field(
        default_factory=CrossVenueDislocationConfig
    )
    allow_short: bool = False
    use_executor_exit_model: bool = False
    ignore_signal_sells: bool = False
    strategy_classes: list[type[BaseStrategy]] = field(default_factory=list)
    strategy_configs: list[Mapping[str, object] | None] = field(default_factory=list)
    aggregator_config: Mapping[str, object] = field(default_factory=dict)
    time_stop_minutes: float = 0
    replay_sentiment_path: str | None = None
    replay_sentiment_max_age_seconds: float | None = None
    futures_mode: bool = False
    futures_leverage: int = 5
    futures_funding_rate: float = DEFAULT_FUTURES_FUNDING_RATE
    funding_cadence: FundingCadence = "scaled_8h"
    fixed_notional_usdt: float = 0.0
    quantity_step_size: float = 0.0
    min_notional_usdt: float = 0.0
    execution_profile: ExecutionProfile = "legacy_v1"


@dataclass
class Trade:
    """A completed simulated position."""

    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    exit_reason: str = "SIGNAL"
    margin_used: float = 0.0
    signal_time: str | None = None
    fill_source: str = "signal_close"
    funding_paid: float = 0.0


@dataclass
class BacktestResult:
    """Aggregate simulator outcome and completed trades."""

    total_return: float
    total_return_pct: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    trades: list[Trade]
    final_equity: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    avg_win_loss_ratio: float
    blocked_buy_count: int = 0
    basis_blocked_buy_count: int = 0
    dislocation_blocked_buy_count: int = 0
    queued_signal_count: int = 0
    unfilled_signal_count: int = 0
    funding_settlement_count: int = 0
