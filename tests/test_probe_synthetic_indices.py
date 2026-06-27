"""Unit tests for the Deriv synthetic-index Gate-1 probe."""

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.probe_synthetic_indices import (
    BLOCKED_ON_DATA,
    INSTRUMENTS,
    ActiveSymbol,
    Candle,
    InstrumentAudit,
    InstrumentResult,
    apply_gates,
    block_sign_null,
    build_observations,
    config_from_args,
    day_concentration,
    default_config,
    holm_adjust,
    parse_args,
    resolve_universe,
)


def _candles(count: int, *, step: float = 1.0, start_epoch: int = 1_700_000_000) -> list[Candle]:
    rows: list[Candle] = []
    price = 100.0
    for index in range(count):
        next_price = price + step
        rows.append(
            Candle(
                epoch=start_epoch + index * 60,
                open=price,
                high=max(price, next_price),
                low=min(price, next_price),
                close=next_price,
            )
        )
        price = next_price
    return rows


def _result(
    *,
    key: str,
    family: str,
    p_raw: float,
    net_mean_bps: float = 5.0,
    first_half_mean_bps: float = 5.0,
    second_half_mean_bps: float = 5.0,
) -> InstrumentResult:
    return InstrumentResult(
        key=key,
        display_name=key,
        symbol=key.upper(),
        family=family,
        hypothesis="test",
        calibration_samples=200,
        forward_samples=200,
        calibration_mean_bps=5.0,
        forward_mean_bps=25.0,
        net_mean_bps=net_mean_bps,
        null_median_bps=0.0,
        p_raw=p_raw,
        p_adj=1.0,
        max_day_concentration=0.10,
        first_half_mean_bps=first_half_mean_bps,
        second_half_mean_bps=second_half_mean_bps,
        beats_null=True,
        sample_ok=True,
        concentration_ok=True,
        statistical_pass=False,
        economic_pass=False,
        family_pass_count=0,
        family_breadth_ok=False,
        gate_pass=False,
    )


def test_frozen_universe_contains_all_ten_requested_instruments() -> None:
    assert len(INSTRUMENTS) == 10
    assert {spec.display_name for spec in INSTRUMENTS} == {
        "Volatility 75 Index",
        "Volatility 100 Index",
        "Volatility 50 Index",
        "Crash 1000 Index",
        "Boom 1000 Index",
        "Crash 500 Index",
        "Boom 500 Index",
        "Step Index",
        "Jump 100 Index",
        "Range Break 100 Index",
    }


def test_resolve_universe_uses_exact_normalized_display_names() -> None:
    payload = [
        {"symbol": "R_75", "display_name": "Volatility 75 Index"},
        {"symbol": "1HZ75V", "display_name": "Volatility 75 (1s) Index"},
    ]
    resolved, errors = resolve_universe(payload, specs=(INSTRUMENTS[0],))
    assert resolved == {"volatility_75": ActiveSymbol("R_75", "Volatility 75 Index")}
    assert errors == []


def test_resolve_universe_supports_current_underlying_symbol_schema() -> None:
    step = next(item for item in INSTRUMENTS if item.key == "step")
    payload = [
        {
            "underlying_symbol": "stpRNG",
            "underlying_symbol_name": "Step Index 100",
        }
    ]
    resolved, errors = resolve_universe(payload, specs=(step,))
    assert resolved == {"step": ActiveSymbol("stpRNG", "Step Index 100")}
    assert errors == []


def test_resolve_universe_reports_missing_instrument() -> None:
    resolved, errors = resolve_universe([], specs=(INSTRUMENTS[0],))
    assert resolved == {}
    assert errors == ["Volatility 75 Index: not returned by active_symbols"]


def test_momentum_observations_are_forward_only_and_non_overlapping() -> None:
    spec = INSTRUMENTS[0]
    candles = _candles(120)
    observations = build_observations(
        candles,
        spec,
        0,
        len(candles),
        threshold_end_index=48,
    )
    assert observations
    assert all(row.signal == 1 for row in observations)
    assert all(row.oriented_return_bps > 0 for row in observations)
    assert all(
        right.epoch - left.epoch >= spec.horizon_bars * 60
        for left, right in zip(observations, observations[1:], strict=False)
    )


def test_step_reversion_orients_against_prior_move() -> None:
    spec = next(item for item in INSTRUMENTS if item.key == "step")
    candles = _candles(20, step=1.0)
    observations = build_observations(
        candles,
        spec,
        0,
        len(candles),
        threshold_end_index=8,
    )
    assert observations
    assert all(row.signal == -1 for row in observations)
    assert all(row.oriented_return_bps < 0 for row in observations)


def test_block_sign_null_rejects_constant_positive_edge() -> None:
    null_median, p_value = block_sign_null(
        [2.0] * 400,
        resamples=1000,
        seed=42,
    )
    assert null_median == pytest.approx(0.0, abs=0.3)
    assert p_value < 0.01


def test_holm_adjust_is_monotonic_and_corrects_family() -> None:
    adjusted = holm_adjust([0.001, 0.02, 0.04])
    assert adjusted == pytest.approx([0.003, 0.04, 0.04])


def test_day_concentration_uses_absolute_edge_mass() -> None:
    rows = [
        replace(
            build_observations(
                _candles(4, start_epoch=1_704_067_200),
                next(item for item in INSTRUMENTS if item.key == "step"),
                0,
                4,
                threshold_end_index=2,
            )[0],
            oriented_return_bps=-10.0,
        ),
        replace(
            build_observations(
                _candles(4, start_epoch=1_704_153_600),
                next(item for item in INSTRUMENTS if item.key == "step"),
                0,
                4,
                threshold_end_index=2,
            )[0],
            oriented_return_bps=30.0,
        ),
    ]
    assert day_concentration(rows) == pytest.approx(0.75)


def test_apply_gates_requires_family_breadth() -> None:
    config = replace(default_config(), round_trip_cost_bps=20.0)
    results = [
        _result(key="v50", family="volatility", p_raw=0.001),
        _result(key="v75", family="volatility", p_raw=0.001),
        _result(key="v100", family="volatility", p_raw=0.5),
    ]
    gated = apply_gates(results, config)
    assert [row.gate_pass for row in gated] == [True, True, False]
    assert all(row.family_pass_count == 2 for row in gated)


def test_apply_gates_requires_both_forward_halves_for_singleton() -> None:
    config = default_config()
    result = _result(
        key="step",
        family="step",
        p_raw=0.001,
        second_half_mean_bps=-1.0,
    )
    [gated] = apply_gates([result], config)
    assert gated.economic_pass is True
    assert gated.family_breadth_ok is False
    assert gated.gate_pass is False


def test_config_rejects_too_few_bootstrap_resamples() -> None:
    args = parse_args(["--bootstrap-resamples", "999"])
    with pytest.raises(ValueError, match="at least 1000"):
        config_from_args(args)


def test_missing_frozen_instrument_is_a_data_block() -> None:
    audit = InstrumentAudit(
        key="volatility_75",
        requested_name="Volatility 75 Index",
        resolved_symbol=None,
        resolved_name=None,
        candles=0,
        start_epoch=None,
        end_epoch=None,
        cache_path=None,
        error="not returned",
    )
    from scripts.probe_synthetic_indices import decide_verdict

    verdict, reasons = decide_verdict([audit], [], default_config())
    assert verdict == BLOCKED_ON_DATA
    assert reasons


def test_partial_mode_does_not_hide_resolved_symbol_fetch_failure() -> None:
    config = replace(default_config(), allow_partial_universe=True)
    audit = InstrumentAudit(
        key="volatility_75",
        requested_name="Volatility 75 Index",
        resolved_symbol="R_75",
        resolved_name="Volatility 75 Index",
        candles=0,
        start_epoch=None,
        end_epoch=None,
        cache_path=None,
        error="history request failed",
    )
    from scripts.probe_synthetic_indices import decide_verdict

    verdict, reasons = decide_verdict([audit], [], config)
    assert verdict == BLOCKED_ON_DATA
    assert reasons == ("Volatility 75 Index: history request failed",)
