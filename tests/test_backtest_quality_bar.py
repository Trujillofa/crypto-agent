"""Adversarial coverage for backtest cost book, causality, split, and live-go."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime
from pathlib import Path

import pytest

from src.backtest.artifacts import create_manifest, write_manifest
from src.backtest.engine import BacktestConfig, BacktestEngine
from src.backtest.experiment_autopilot import (
    WfoWindow,
    build_wfo_windows,
    wfo_inclusive_fetch_bounds,
)
from src.backtest.factory import BacktestRequest, build_backtest_config
from src.backtest.metrics import calculate_backtest_metrics
from src.backtest.models import BacktestResult, Trade
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


def _tracking_reader(rows: list[dict[str, object]]) -> tuple[IndicatorReader, list[str]]:
    reader = IndicatorReader({})
    calls: list[str] = []

    async def fetch_range(*_args: object) -> list[dict[str, object]]:
        calls.append("fetch_range")
        return rows

    async def fetch_multi_timeframe(**_kwargs: object) -> list[dict[str, object]]:
        calls.append("fetch_multi_timeframe")
        return rows

    reader.fetch_range = fetch_range  # type: ignore[method-assign]
    reader.fetch_multi_timeframe = fetch_multi_timeframe  # type: ignore[method-assign]
    return reader, calls


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _assert_half_open_disjoint(
    left_start: datetime, left_end: datetime, right_start: datetime, right_end: datetime
) -> None:
    """[start, end) intervals share a boundary instant without interior overlap."""
    assert left_start < left_end
    assert right_start < right_end
    assert left_end <= right_start or right_end <= left_start


def _assert_windows_disjoint(windows: list[WfoWindow]) -> None:
    assert windows
    for window in windows:
        train_start = _parse_iso(window.train_start)
        train_end = _parse_iso(window.train_end)
        test_start = _parse_iso(window.test_start)
        test_end = _parse_iso(window.test_end)
        assert train_end == test_start
        _assert_half_open_disjoint(train_start, train_end, test_start, test_end)
    for previous, current in zip(windows, windows[1:], strict=False):
        prev_train_end = _parse_iso(previous.train_end)
        next_train_start = _parse_iso(current.train_start)
        assert prev_train_end == next_train_start
        _assert_half_open_disjoint(
            _parse_iso(previous.train_start),
            prev_train_end,
            next_train_start,
            _parse_iso(current.train_end),
        )
        _assert_half_open_disjoint(
            _parse_iso(previous.test_start),
            _parse_iso(previous.test_end),
            _parse_iso(current.test_start),
            _parse_iso(current.test_end),
        )


def _base_trade(**overrides: object) -> Trade:
    payload: dict[str, object] = {
        "entry_time": "2024-01-01T00:00:00",
        "exit_time": "2024-01-01T01:00:00",
        "side": "BUY",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "quantity": 1.0,
        "pnl": 1.0,
        "return_pct": 1.0,
        "exit_reason": "SIGNAL",
        "margin_used": 0.0,
        "signal_time": "2024-01-01T00:00:00",
        "fill_source": "signal_close",
        "funding_paid": 0.0,
    }
    payload.update(overrides)
    return Trade(**payload)  # type: ignore[arg-type]


def _result_from_trades(trades: list[Trade], *, total_return: float = 1.0) -> BacktestResult:
    return BacktestResult(
        total_return=total_return,
        total_return_pct=0.1,
        max_drawdown=0.0,
        win_rate=100.0 if trades else 0.0,
        total_trades=len(trades),
        trades=trades,
        final_equity=10_001.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        profit_factor=1.0,
        avg_win_loss_ratio=1.0,
    )


def _sample_config() -> BacktestConfig:
    return BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2024-01-01",
        end_date="2024-01-02",
    )


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
    result = _result_from_trades(
        [
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
        ]
    )
    manifest = create_manifest(config=_sample_config(), result=result)
    assert "trades" not in manifest.result
    assert manifest.result["total_trades"] == 1
    assert manifest.trades_fingerprint
    assert len(manifest.trades_fingerprint) == 64


def test_selection_train_end_equals_first_wfo_test_start() -> None:
    windows = build_wfo_windows("2024-01-01", "2026-01-01", 6, 3)
    assert windows
    assert windows[0].train_end.startswith("2024-07-01")
    assert windows[0].test_start.startswith("2024-07-01")
    assert windows[0].train_end == windows[0].test_start


def test_wfo_train_and_test_intervals_are_disjoint() -> None:
    windows = build_wfo_windows("2024-01-01", "2026-01-01", 6, 3)
    _assert_windows_disjoint(windows)


def _inclusive_reader_contains(row_time: str, start: str, end: str) -> bool:
    """Mirror IndicatorReader SQL: i.time >= start AND i.time <= end."""
    value = _parse_iso(row_time)
    return _parse_iso(start) <= value <= _parse_iso(end)


def _inclusive_sql_reader(rows: list[dict[str, object]]) -> IndicatorReader:
    reader = IndicatorReader({})
    fetched: list[list[dict[str, object]]] = []

    async def fetch_range(
        _symbol: str, _timeframe: str, start_time: str, end_time: str
    ) -> list[dict[str, object]]:
        selected = [
            row
            for row in rows
            if _inclusive_reader_contains(str(row["time"]), start_time, end_time)
        ]
        fetched.append(selected)
        return selected

    reader.fetch_range = fetch_range  # type: ignore[method-assign]
    reader.fetched_ranges = fetched  # type: ignore[attr-defined]
    return reader


def test_search_scripts_use_identical_calendar_wfo_boundaries() -> None:
    config_src = Path("scripts/run_config_search.py").read_text(encoding="utf-8")
    mtf_src = Path("scripts/run_mtf_search.py").read_text(encoding="utf-8")
    autopilot_src = Path("scripts/experiment_autopilot.py").read_text(encoding="utf-8")
    for src in (config_src, mtf_src):
        assert "build_wfo_windows(start, end, train_months, test_months)" in src
        assert "wfo_inclusive_fetch_bounds(" in src
        assert "for window in windows:" in src
        assert "timedelta(days=" not in src
        assert "months * 30" not in src
        assert "train_months * 30" not in src
        assert "test_months * 30" not in src
    assert "wfo_inclusive_fetch_bounds(window)" in autopilot_src
    assert "                    start=window.test_start," not in autopilot_src
    assert "                    start=test_start," in autopilot_src
    assert "                    end=test_end," in autopilot_src
    windows = build_wfo_windows("2024-01-01", "2026-01-01", 6, 3)
    sequence = [(window.test_start, window.test_end) for window in windows]
    assert sequence
    assert sequence[0][0].startswith("2024-07-01")
    assert sequence[0][1].startswith("2024-10-01")
    assert sequence == [
        (window.test_start, window.test_end)
        for window in build_wfo_windows("2024-01-01", "2026-01-01", 6, 3)
    ]


def test_leap_year_and_month_end_wfo_windows_remain_disjoint() -> None:
    cases = [
        ("2024-01-31", "2025-01-31", 3, 1),
        ("2023-11-30", "2025-01-31", 3, 1),
    ]
    covering_feb29 = False
    for start, end, train_months, test_months in cases:
        windows = build_wfo_windows(start, end, train_months, test_months)
        _assert_windows_disjoint(windows)
        for window in windows:
            train_start = _parse_iso(window.train_start)
            test_end = _parse_iso(window.test_end)
            if train_start <= datetime(2024, 2, 29) < test_end:
                covering_feb29 = True
            for stamp in (
                window.train_start,
                window.train_end,
                window.test_start,
                window.test_end,
            ):
                if stamp.startswith("2024-02-29"):
                    covering_feb29 = True
    assert covering_feb29


def test_indicator_reader_sql_range_is_inclusive() -> None:
    source = Path("src/features/reader.py").read_text(encoding="utf-8")
    assert "i.time >= $3 AND i.time <= $4" in source


def test_raw_shared_window_end_leaks_under_inclusive_reader() -> None:
    windows = build_wfo_windows("2024-01-01", "2026-01-01", 6, 3)
    window = windows[0]
    boundary = "2024-07-01T00:00:00"
    assert window.train_end.startswith("2024-07-01")
    assert window.test_start.startswith("2024-07-01")
    train_inclusive = _inclusive_reader_contains(boundary, window.train_start, window.train_end)
    test_inclusive = _inclusive_reader_contains(boundary, window.test_start, window.test_end)
    assert train_inclusive is True
    assert test_inclusive is True


@pytest.mark.asyncio
async def test_boundary_bar_appears_only_in_wfo_test_dataset() -> None:
    windows = build_wfo_windows("2024-01-01", "2026-01-01", 6, 3)
    window = windows[0]
    boundary = "2024-07-01T00:00:00"
    rows = [
        {"time": "2024-06-30T23:00:00", "open_price": 1.0, "close_price": 1.0},
        {"time": boundary, "open_price": 2.0, "close_price": 2.0},
        {"time": "2024-07-01T01:00:00", "open_price": 3.0, "close_price": 3.0},
    ]
    train_start, train_end, test_start, test_end = wfo_inclusive_fetch_bounds(window)
    train_inclusive = _inclusive_reader_contains(boundary, train_start, train_end)
    test_inclusive = _inclusive_reader_contains(boundary, test_start, test_end)
    assert train_inclusive is False
    assert test_inclusive is True

    reader = _inclusive_sql_reader(rows)
    train_rows = await reader.fetch_range("SOLUSDT", "1h", train_start, train_end)
    test_rows = await reader.fetch_range("SOLUSDT", "1h", test_start, test_end)
    train_times = [row["time"] for row in train_rows]
    test_times = [row["time"] for row in test_rows]
    assert boundary not in train_times
    assert boundary in test_times
    assert "2024-06-30T23:00:00" in train_times
    assert "2024-06-30T23:00:00" not in test_times
    assert "2024-07-01T01:00:00" not in train_times
    assert "2024-07-01T01:00:00" in test_times

    train_reader = _inclusive_sql_reader(rows)
    test_reader = _inclusive_sql_reader(rows)
    await BacktestEngine(
        BacktestConfig(
            symbol="SOLUSDT",
            timeframe="1h",
            start_date=train_start,
            end_date=train_end,
        ),
        train_reader,
    ).run()
    await BacktestEngine(
        BacktestConfig(
            symbol="SOLUSDT",
            timeframe="1h",
            start_date=test_start,
            end_date=test_end,
        ),
        test_reader,
    ).run()
    engine_train_times = [str(row["time"]) for row in train_reader.fetched_ranges[0]]
    engine_test_times = [str(row["time"]) for row in test_reader.fetched_ranges[0]]
    assert boundary not in engine_train_times
    assert boundary in engine_test_times


def _wfo_test_config(start: str, end: str) -> BacktestConfig:
    return BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date=start,
        end_date=end,
        fee_rate=0.0,
        slippage_pct=0.0,
        apply_global_trend_filter=False,
        execution_profile="execution_parity_v2",
        strategy_classes=[BuyOnClose100],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5, "sell_threshold": -0.5},
    )


@pytest.mark.asyncio
async def test_canonical_wfo_excludes_exclusive_test_end_fill() -> None:
    """A fill sitting on test_end is OOS contamination under inclusive SQL.

    Autopilot used to pass raw window.test_end into fetch_range (time <= end).
    v2 fills at the next bar open, so a signal on the last in-window bar plus a
    bar exactly at test_end completes a trade that a half-open window would
    leave unfilled.
    """
    windows = build_wfo_windows("2024-01-01", "2026-01-01", 6, 3)
    window = windows[0]
    signal_time = "2024-09-30T23:00:00"
    fill_time = window.test_end
    if "T" not in fill_time:
        fill_time = f"{fill_time}T00:00:00"
    rows = [
        {"time": signal_time, "open_price": 99.0, "close_price": 100.0},
        {"time": fill_time, "open_price": 101.0, "close_price": 102.0},
    ]
    raw = await BacktestEngine(
        _wfo_test_config(window.test_start, window.test_end),
        _inclusive_sql_reader(rows),
    ).run()
    _, _, test_start, test_end = wfo_inclusive_fetch_bounds(window)
    corrected = await BacktestEngine(
        _wfo_test_config(test_start, test_end),
        _inclusive_sql_reader(rows),
    ).run()
    assert raw.total_trades == 1
    assert corrected.total_trades == 0
    assert corrected.unfilled_signal_count == 1


def test_identical_trade_traces_share_fingerprint_and_payload() -> None:
    trades = [_base_trade()]
    first = create_manifest(config=_sample_config(), result=_result_from_trades(trades))
    second = create_manifest(config=_sample_config(), result=_result_from_trades([_base_trade()]))
    assert first.trades_fingerprint == second.trades_fingerprint
    assert first.run_id == second.run_id
    from src.backtest.artifacts import canonical_json

    assert canonical_json(first) == canonical_json(second)


def test_divergent_traces_keep_run_id_and_conflict_on_write(tmp_path: Path) -> None:
    config = _sample_config()
    first = create_manifest(
        config=config,
        result=_result_from_trades(
            [
                _base_trade(
                    fill_source="signal_close",
                    signal_time="2024-01-01T00:00:00",
                    entry_price=100.0,
                    exit_price=101.0,
                )
            ]
        ),
        revision="abc123",
        data_fingerprint="data",
        seed=7,
        source_config="config/settings.yaml",
    )
    second = create_manifest(
        config=config,
        result=_result_from_trades(
            [
                _base_trade(
                    fill_source="next_bar_open",
                    signal_time="2024-01-01T00:00:01",
                    entry_price=100.5,
                    exit_price=101.5,
                )
            ]
        ),
        revision="abc123",
        data_fingerprint="data",
        seed=7,
        source_config="config/settings.yaml",
    )
    assert first.run_id == second.run_id
    assert first.trades_fingerprint != second.trades_fingerprint
    write_manifest(tmp_path, first)
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        write_manifest(tmp_path, second)


def test_reordering_trades_changes_fingerprint() -> None:
    first = _base_trade(entry_time="2024-01-01T00:00:00", exit_time="2024-01-01T01:00:00")
    second = _base_trade(entry_time="2024-01-02T00:00:00", exit_time="2024-01-02T01:00:00")
    ordered = create_manifest(config=_sample_config(), result=_result_from_trades([first, second]))
    reversed_order = create_manifest(
        config=_sample_config(), result=_result_from_trades([second, first])
    )
    assert ordered.trades_fingerprint != reversed_order.trades_fingerprint


def test_empty_trade_list_fingerprint_is_stable() -> None:
    first = create_manifest(config=_sample_config(), result=_result_from_trades([]))
    second = create_manifest(config=_sample_config(), result=_result_from_trades([]))
    nonempty = create_manifest(config=_sample_config(), result=_result_from_trades([_base_trade()]))
    assert first.trades_fingerprint == second.trades_fingerprint
    assert first.trades_fingerprint != nonempty.trades_fingerprint
    assert len(first.trades_fingerprint) == 64


def test_trades_fingerprint_covers_every_trade_field() -> None:
    base = _base_trade()
    base_fp = create_manifest(
        config=_sample_config(), result=_result_from_trades([base])
    ).trades_fingerprint
    variants: dict[str, object] = {
        "entry_time": "2024-02-01T00:00:00",
        "exit_time": "2024-02-01T02:00:00",
        "side": "SELL",
        "entry_price": 200.0,
        "exit_price": 180.0,
        "quantity": 2.0,
        "pnl": -20.0,
        "return_pct": -10.0,
        "exit_reason": "STOP",
        "margin_used": 50.0,
        "signal_time": "2024-01-31T23:00:00",
        "fill_source": "next_bar_open",
        "funding_paid": 0.25,
    }
    covered = {field.name for field in fields(Trade)}
    assert covered == set(variants)
    for name, value in variants.items():
        mutated = replace(base, **{name: value})
        fingerprint = create_manifest(
            config=_sample_config(), result=_result_from_trades([mutated])
        ).trades_fingerprint
        assert fingerprint != base_fp, name


@pytest.mark.asyncio
async def test_engine_rejects_unknown_timeframe_before_empty_data_success() -> None:
    reader, calls = _tracking_reader([])
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="97m",
        start_date="2024-01-01",
        end_date="2024-01-02",
    )
    with pytest.raises(ValueError, match="Unsupported timeframe: 97m"):
        await BacktestEngine(config, reader).run()
    assert calls == []


@pytest.mark.asyncio
async def test_engine_empty_data_still_returns_zero_trade_result() -> None:
    result = await BacktestEngine(_sample_config(), _reader([])).run()
    assert result.total_trades == 0
    assert result.trades == []
    assert result.total_return == 0.0
    assert result.final_equity == _sample_config().initial_capital


@pytest.mark.asyncio
async def test_engine_completes_one_bar_and_multi_bar_runs() -> None:
    one_bar = [{"time": "2024-01-01T00:00:00", "open_price": 99.0, "close_price": 100.0}]
    multi_bar = [
        {"time": "2024-01-01T00:00:00", "open_price": 99.0, "close_price": 100.0},
        {"time": "2024-01-01T01:00:00", "open_price": 100.0, "close_price": 101.0},
        {"time": "2024-01-01T02:00:00", "open_price": 101.0, "close_price": 102.0},
    ]
    one = await BacktestEngine(_sample_config(), _reader(one_bar)).run()
    many = await BacktestEngine(_sample_config(), _reader(multi_bar)).run()
    assert one.total_trades == 0
    assert many.total_trades == 0


class _InvalidMtfStrategy(BaseStrategy):
    REQUIRED_TIMEFRAMES = {"entry": "97m", "regime": "4h"}

    def get_name(self) -> str:
        return "InvalidMtf"

    async def evaluate(self, symbol: str, indicators: dict[str, object]) -> Signal:
        price = float(indicators["close_price"])
        return Signal(SignalType.HOLD, symbol, price, 0.0, "hold", indicators)


@pytest.mark.asyncio
async def test_engine_rejects_invalid_mtf_timeframe_before_fetch() -> None:
    reader, calls = _tracking_reader([])
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2024-01-01",
        end_date="2024-01-02",
        strategy_classes=[_InvalidMtfStrategy],
    )
    with pytest.raises(ValueError, match="Unsupported timeframe: 97m"):
        await BacktestEngine(config, reader).run()
    assert "fetch_range" not in calls
    assert "fetch_multi_timeframe" not in calls


RESEARCH_CLIS = [
    "scripts/run_backtest.py",
    "scripts/experiment_autopilot.py",
    "scripts/run_wfo.py",
    "scripts/run_config_search.py",
    "scripts/run_mtf_search.py",
    "scripts/run_full_backtest.py",
]


@pytest.mark.parametrize("script", RESEARCH_CLIS)
def test_research_cli_refuses_live_flag_when_actually_invoked(script: str) -> None:
    """The refusal must fire in a real process, not just in a unit call.

    ``refuse_live_go`` used to run after ``parse_args()``, so argparse exited
    with "unrecognized arguments: --live" and ``LiveGoRefused`` never raised.
    The unit test still passed. Only invoking the script proves the guard is
    reachable, so assert on the refusal message rather than on exit status.
    """
    completed = subprocess.run(
        [sys.executable, script, "--live"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode != 0
    assert "not a live-go" in completed.stderr, completed.stderr
    assert "unrecognized arguments" not in completed.stderr
