"""Single construction path from resolved settings to a backtest configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.backtest.cost_overrides import (
    DEFAULT_FUTURES_FUNDING_RATE,
    REALISTIC_FEE_RATE,
    REALISTIC_SLIPPAGE_PCT,
    CostProfile,
    FundingCadence,
)
from src.backtest.models import BacktestConfig, ExecutionProfile
from src.strategy.basis_premium_filter import (
    BasisPremiumFilterConfig,
    parse_basis_premium_filter,
    with_calibrated_threshold,
)
from src.strategy.cross_venue_dislocation import (
    CrossVenueDislocationConfig,
    parse_cross_venue_dislocation,
)
from src.strategy.session_liquidity import parse_session_liquidity_router


@dataclass(frozen=True)
class BacktestRequest:
    """Auditable inputs used to resolve a simulator configuration.

    It deliberately stores only stable, serialisable run intent.  Runtime
    settings and strategy classes remain inputs to the factory so callers do
    not have to duplicate the policy that translates them to engine options.
    """

    symbol: str
    timeframe: str
    start: str
    end: str
    initial_capital: float = 10_000.0
    allow_short: bool = False
    disable_trend_filter: bool = False
    trend_filter_override: bool | None = None
    replay_sentiment_path: str | None = None
    replay_sentiment_max_age_hours: float | None = None
    fixed_notional_usdt: float = 0.0
    quantity_step_size: float = 0.0
    min_notional_usdt: float = 0.0
    execution_profile: ExecutionProfile = "legacy_v1"


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def config_explicit_trend_filter(raw_config: Mapping[str, object]) -> bool | None:
    strategy = _mapping(raw_config.get("strategy"))
    if "global_trend_filter_enabled" not in strategy:
        return None
    return bool(strategy["global_trend_filter_enabled"])


def resolve_global_trend_filter(
    *,
    raw_config: Mapping[str, object],
    disable_trend_filter: bool,
    cost_profile: CostProfile | None,
    trend_filter_override: bool | None = None,
) -> tuple[bool, str, bool | None]:
    """Resolve the trend gate once and record the source of the decision."""
    config_explicit = config_explicit_trend_filter(raw_config)
    if trend_filter_override is not None:
        return trend_filter_override, "caller_override", config_explicit
    if cost_profile is not None:
        return cost_profile.apply_global_trend_filter, "cost_profile_override", config_explicit
    if disable_trend_filter:
        return False, "cli_override", config_explicit
    if config_explicit is not None:
        return config_explicit, "config", config_explicit
    return True, "engine_default", config_explicit


def build_backtest_config(
    *,
    request: BacktestRequest,
    settings: Any,
    raw_config: Mapping[str, object],
    strategy_classes: list[type],
    strategy_configs: list[Mapping[str, object] | None],
    aggregator_config: Mapping[str, object],
    cost_profile: CostProfile | None = None,
    fee_rate: float | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    basis_calibrated_threshold: float | None = None,
    cross_venue_dislocation: CrossVenueDislocationConfig | None = None,
    futures_mode: bool | None = None,
) -> BacktestConfig:
    """Build a complete engine config without caller-specific policy drift."""
    trading_execution = _mapping(raw_config.get("trading_execution"))
    exit_rules = _mapping(trading_execution.get("exit_rules"))
    strategy = _mapping(raw_config.get("strategy"))
    trend_filter, trend_source, config_trend_filter = resolve_global_trend_filter(
        raw_config=raw_config,
        disable_trend_filter=request.disable_trend_filter,
        cost_profile=cost_profile,
        trend_filter_override=request.trend_filter_override,
    )

    futures_settings = getattr(settings, "futures", None)
    if futures_mode is None:
        futures_mode = bool(
            futures_settings
            and futures_settings.enabled
            and request.symbol in futures_settings.symbols
        )
    leverage = int(getattr(futures_settings, "default_leverage", 5))
    resolved_fee_rate = (
        fee_rate
        if fee_rate is not None
        else cost_profile.fee_rate
        if cost_profile is not None
        else REALISTIC_FEE_RATE
    )
    slippage_pct = cost_profile.slippage_pct if cost_profile is not None else REALISTIC_SLIPPAGE_PCT
    funding_rate = (
        cost_profile.base_futures_funding_rate
        if cost_profile is not None
        else DEFAULT_FUTURES_FUNDING_RATE
    )
    funding_cadence: FundingCadence = (
        cost_profile.funding_cadence if cost_profile is not None else "scaled_8h"
    )

    return BacktestConfig(
        symbol=request.symbol,
        timeframe=request.timeframe,
        start_date=request.start,
        end_date=request.end,
        initial_capital=request.initial_capital,
        fee_rate=resolved_fee_rate,
        stop_loss_pct=(
            settings.trading_execution.stop_loss_pct if stop_loss_pct is None else stop_loss_pct
        ),
        take_profit_pct=(
            settings.trading_execution.take_profit_pct
            if take_profit_pct is None
            else take_profit_pct
        ),
        sl_atr_multiplier=float(trading_execution.get("sl_atr_multiplier", 2.0)),
        tp_atr_multiplier=float(trading_execution.get("tp_atr_multiplier", 4.5)),
        trailing_activate_atr=float(trading_execution.get("trailing_activate_atr", 1.5)),
        trailing_offset_atr=float(trading_execution.get("trailing_offset_atr", 1.0)),
        slippage_pct=slippage_pct,
        use_atr_sizing=settings.trading_execution.use_atr_sizing,
        atr_multiplier=settings.trading_execution.atr_multiplier,
        risk_per_trade=settings.trading_execution.risk_per_trade_pct,
        apply_global_trend_filter=trend_filter,
        global_trend_filter_buffer_pct=float(strategy.get("global_trend_filter_buffer_pct", 0.0)),
        global_trend_filter_source=trend_source,
        config_global_trend_filter_enabled=config_trend_filter,
        session_liquidity_router=parse_session_liquidity_router(
            strategy.get("session_liquidity_router")
        ),
        basis_premium_filter=with_calibrated_threshold(
            parse_basis_premium_filter(strategy.get("basis_premium_filter")),
            basis_calibrated_threshold,
        ),
        cross_venue_dislocation=cross_venue_dislocation
        or parse_cross_venue_dislocation(strategy.get("cross_venue_dislocation")),
        allow_short=request.allow_short,
        use_executor_exit_model=bool(exit_rules.get("backtest_use_executor_exit_model", False)),
        ignore_signal_sells=bool(exit_rules.get("backtest_ignore_signal_sells", False)),
        time_stop_minutes=float(exit_rules.get("time_stop_minutes", 0)),
        replay_sentiment_path=request.replay_sentiment_path,
        replay_sentiment_max_age_seconds=(
            request.replay_sentiment_max_age_hours * 3600
            if request.replay_sentiment_max_age_hours is not None
            else None
        ),
        futures_mode=futures_mode,
        futures_leverage=leverage,
        futures_funding_rate=funding_rate,
        funding_cadence=funding_cadence,
        fixed_notional_usdt=request.fixed_notional_usdt,
        quantity_step_size=request.quantity_step_size,
        min_notional_usdt=request.min_notional_usdt,
        execution_profile=request.execution_profile,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
    )


def parsed_basis_filter(raw_config: Mapping[str, object]) -> BasisPremiumFilterConfig:
    """Expose the shared parsed filter to WFO calibration callers."""
    return parse_basis_premium_filter(
        _mapping(raw_config.get("strategy")).get("basis_premium_filter")
    )
