"""Unit tests for liquidation cascade probe helpers."""

from datetime import UTC, datetime

from scripts.probe_liquidation_cascade import (
    DataAudit,
    MetricsRow,
    _day_concentration,
    _oriented_return,
    decide_verdict,
    default_config,
    detect_cascade_events,
)


def test_detect_cascade_events_long_side() -> None:
    config = default_config()
    base = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    metrics = [
        MetricsRow(base, "BTCUSDT", 1000.0, 1e9, 1.0),
        MetricsRow(base.replace(minute=5), "BTCUSDT", 998.0, 9.9e8, 0.40),
    ]
    events = detect_cascade_events(metrics, config)
    assert len(events) == 1
    assert events[0].side == "LONG_CASCADE"


def test_oriented_return_fade_vs_continuation() -> None:
    assert _oriented_return("LONG_CASCADE", "fade", 50.0) == 50.0
    assert _oriented_return("LONG_CASCADE", "continuation", 50.0) == -50.0


def test_day_concentration_uses_absolute_mass() -> None:
    ts = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    conc = _day_concentration([(ts, 10.0), (ts.replace(hour=13), -5.0)])
    assert 0.0 < conc <= 1.0


def test_decide_verdict_no_pulse_when_no_gate_pass() -> None:
    config = default_config()
    audit = DataAudit(
        rest_all_force_orders="deprecated",
        websocket_path="ws",
        metrics_days_loaded=14,
        force_orders_collected=0,
        force_orders_cached=0,
        events_per_symbol={"BTCUSDT": 10, "ETHUSDT": 10, "SOLUSDT": 10},
        blocked=False,
        blocked_reason=None,
    )
    verdict, reasons, best = decide_verdict((), audit, config)
    assert verdict == "NO_PULSE"
    assert best is None
    assert reasons
