from __future__ import annotations

from datetime import datetime, timedelta

from scripts.probe_relative_strength_rotation import (
    ProbeConfig,
    align_same_timestamp_rows,
    probe_rows,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "target_symbol": "ETHUSDT",
        "anchor_symbol": "BTCUSDT",
        "timeframe": "1h",
        "start": "2024-01-01T00:00:00",
        "end": "2024-01-03T00:00:00",
        "fast_lookback_bars": 2,
        "slow_lookback_bars": 4,
        "forward_bars": 2,
        "min_fast_rs_pct": 1.0,
        "min_slow_rs_pct": 2.0,
        "max_rs_deterioration_pct": -1.5,
        "max_pullback_distance_pct": 3.0,
        "rsi_reset_min": 35.0,
        "rsi_reset_max": 65.0,
        "anchor_max_fast_loss_pct": 3.0,
        "anchor_min_ema200_distance_pct": -5.0,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _row(
    index: int,
    close: float,
    *,
    ema_50: float | None = None,
    ema_200: float | None = None,
    vwap: float | None = None,
    rsi_14: float = 50.0,
) -> dict[str, object]:
    row_time = datetime(2024, 1, 1) + timedelta(hours=index)
    return {
        "time": row_time,
        "close_price": close,
        "ema_50": close if ema_50 is None else ema_50,
        "ema_200": close if ema_200 is None else ema_200,
        "vwap": close if vwap is None else vwap,
        "rsi_14": rsi_14,
    }


def test_align_same_timestamp_rows_uses_only_matching_closed_bars() -> None:
    target_rows = [_row(0, 100.0), _row(1, 101.0), _row(2, 102.0)]
    anchor_rows = [_row(0, 50.0), _row(2, 51.0), _row(3, 52.0)]

    aligned_target, aligned_anchor = align_same_timestamp_rows(target_rows, anchor_rows)

    assert [row["time"] for row in aligned_target] == [
        target_rows[0]["time"],
        target_rows[2]["time"],
    ]
    assert [row["time"] for row in aligned_anchor] == [
        anchor_rows[0]["time"],
        anchor_rows[1]["time"],
    ]


def test_probe_rows_counts_events_and_forward_excess_return() -> None:
    target_prices = [100.0, 101.0, 102.0, 104.0, 106.0, 107.0, 110.0, 112.0]
    anchor_prices = [100.0, 100.0, 100.5, 101.0, 101.0, 101.5, 102.0, 102.0]
    target_rows = [
        _row(index, price, ema_50=price * 0.99, ema_200=price * 0.95, vwap=price * 0.995)
        for index, price in enumerate(target_prices)
    ]
    anchor_rows = [
        _row(index, price, ema_50=price, ema_200=price * 0.98, vwap=price)
        for index, price in enumerate(anchor_prices)
    ]

    summary = probe_rows(target_rows, anchor_rows, _config(max_rs_deterioration_pct=-3.0))

    assert summary.aligned_rows == 8
    assert summary.event_count >= 1
    assert summary.mean_forward_return_pct > 0.0
    assert summary.mean_excess_forward_return_pct > 0.0


def test_probe_rows_returns_no_events_when_rs_is_negative() -> None:
    target_prices = [100.0, 100.0, 99.0, 99.0, 98.0, 98.0, 98.0]
    anchor_prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    target_rows = [
        _row(index, price, ema_50=price * 0.99, ema_200=price * 0.95, vwap=price)
        for index, price in enumerate(target_prices)
    ]
    anchor_rows = [
        _row(index, price, ema_50=price, ema_200=price * 0.98, vwap=price)
        for index, price in enumerate(anchor_prices)
    ]

    summary = probe_rows(target_rows, anchor_rows, _config())

    assert summary.event_count == 0
    assert summary.mean_forward_return_pct == 0.0
    assert summary.mean_excess_forward_return_pct == 0.0
