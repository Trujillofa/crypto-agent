from __future__ import annotations

from datetime import datetime, timedelta

from scripts.probe_funding_normalization import (
    FundingTick,
    NormalizationSide,
    ProbeConfig,
    detect_normalization_events,
    evaluate_pulse,
    index_price_bars,
    probe_funding_series,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "symbol": "ETHUSDT",
        "timeframe": "1h",
        "start": "2024-01-01T00:00:00",
        "end": "2024-02-01T00:00:00",
        "entry_threshold": 0.0005,
        "exit_threshold": 0.00015,
        "forward_bars_12h": 2,
        "forward_bars_24h": 4,
        "min_events_for_pulse": 2,
        "max_profit_concentration_pct": 30.0,
        "long_only": True,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _price_rows(
    count: int, start_price: float = 100.0, step: float = 1.0
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        price = start_price + step * index
        rows.append(
            {
                "time": datetime(2024, 1, 1) + timedelta(hours=index),
                "close_price": price,
            }
        )
    return rows


def test_detect_normalization_fires_once_per_negative_cycle() -> None:
    base = datetime(2024, 1, 1)
    ticks = [
        FundingTick(base, -0.0008),
        FundingTick(base + timedelta(hours=8), -0.0007),
        FundingTick(base + timedelta(hours=16), 0.00005),
        FundingTick(base + timedelta(hours=24), 0.00004),
        FundingTick(base + timedelta(hours=32), -0.0009),
        FundingTick(base + timedelta(hours=40), 0.00003),
    ]
    price_rows = _price_rows(48)
    events = detect_normalization_events(
        ticks,
        *index_price_bars(price_rows),
        _config(forward_bars_12h=2, forward_bars_24h=4),
    )

    long_events = [event for event in events if event.side == NormalizationSide.LONG_FROM_NEGATIVE]
    assert len(long_events) == 2
    assert long_events[0].prior_extreme_rate == -0.0008
    assert long_events[1].prior_extreme_rate == -0.0009


def test_probe_funding_series_computes_net_forward_after_drag() -> None:
    base = datetime(2024, 1, 1)
    ticks = [
        FundingTick(base, -0.0006),
        FundingTick(base + timedelta(hours=8), 0.00005),
        FundingTick(base + timedelta(hours=16), 0.0002),
    ]
    price_rows = _price_rows(32, start_price=100.0, step=2.0)
    summary = probe_funding_series(
        ticks,
        price_rows,
        _config(forward_bars_12h=12, forward_bars_24h=16),
    )

    assert len(summary.long_events) == 1
    event = summary.long_events[0]
    assert event.gross_forward_12h_pct > 0
    assert event.funding_drag_12h_pct > 0
    assert event.net_forward_12h_pct < event.gross_forward_12h_pct


def test_evaluate_pulse_sparse_when_below_min_events() -> None:
    summary = probe_funding_series([], _price_rows(4), _config(min_events_for_pulse=5))
    assert evaluate_pulse(summary, _config(min_events_for_pulse=5)) == "NO_PULSE"


def test_evaluate_pulse_has_pulse_with_positive_net_and_enough_events() -> None:
    base = datetime(2024, 1, 1)
    ticks = []
    for cycle in range(3):
        offset = cycle * 48
        ticks.extend(
            [
                FundingTick(base + timedelta(hours=offset), -0.0007),
                FundingTick(base + timedelta(hours=offset + 8), 0.00004),
            ]
        )
    price_rows = _price_rows(200, start_price=100.0, step=0.5)
    summary = probe_funding_series(
        ticks,
        price_rows,
        _config(forward_bars_12h=2, forward_bars_24h=4, min_events_for_pulse=2),
    )
    verdict = evaluate_pulse(
        summary,
        _config(forward_bars_12h=2, forward_bars_24h=4, min_events_for_pulse=2),
    )
    assert verdict in {"HAS_PULSE", "WEAK_EDGE", "CONCENTRATED", "SPARSE"}
    assert len(summary.long_events) >= 2
