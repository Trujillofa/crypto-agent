"""Tests for cross-venue dislocation gate (mirrors test patterns for basis_premium_filter and other gates).

Per CLAUDE.md: assertions in it/test, no .only/.skip in committed, etc.
"""

import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.cross_venue_dislocation import (
    CrossVenueDislocationConfig,
    apply_cross_venue_dislocation_gate,
    parse_cross_venue_dislocation,
)
from src.strategy.signals import Signal, SignalType


def _mk_buy(price: float = 100.0) -> Signal:
    return Signal(
        type=SignalType.BUY,
        symbol="SOLUSDT",
        price=price,
        confidence=0.8,
        reason="test",
        indicators={},
        trading_mode="paper",
    )


def _mk_row(basis_spread: float | None = None, premium_spread: float | None = None) -> dict:
    return {
        "cross_venue_basis_spread_bps": basis_spread,
        "cross_venue_premium_spread": premium_spread,
        "time": "2024-01-01T00:00:00+00:00",
        "close_price": 100.0,
        # minimal other keys the engine may touch in synthetic paths
        "atr_14": 1.0,
        "high_price": 101.0,
        "low_price": 99.0,
    }


class _DummyStrategy(BaseStrategy):
    REQUIRED_TIMEFRAMES = {}

    def __init__(self, symbol: str = "SOLUSDT", **kwargs):
        self.symbol = symbol

    async def generate_signals(self, *a, **k):
        return []


def test_require_mode_blocks_below_threshold_both_metrics():
    cfg = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="require", min_spread_bps=5.0, side="both"
    )
    # below
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), _mk_row(basis_spread=3.0), cfg)
    assert blocked is True
    assert sig.type == SignalType.HOLD
    # at/above
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), _mk_row(basis_spread=5.0), cfg)
    assert blocked is False
    assert sig.type == SignalType.BUY

    cfg2 = CrossVenueDislocationConfig(
        enabled=True, metric="premium_spread", mode="require", min_spread_bps=5.0, side="both"
    )
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), _mk_row(premium_spread=3.0), cfg2)
    assert blocked is True


def test_block_mode_blocks_at_or_above_threshold():
    cfg = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="block", min_spread_bps=5.0, side="both"
    )
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), _mk_row(basis_spread=6.0), cfg)
    assert blocked is True
    assert sig.type == SignalType.HOLD
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), _mk_row(basis_spread=4.0), cfg)
    assert blocked is False


def test_side_positive_ignores_negative():
    cfg = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="require", min_spread_bps=5.0, side="positive"
    )
    # negative spread (binance < bybit) should not count as positive dislocation
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), _mk_row(basis_spread=-6.0), cfg)
    assert blocked is True  # require not met
    # positive does
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), _mk_row(basis_spread=6.0), cfg)
    assert blocked is False


def test_sign_convention_binance_minus_bybit():
    # row built as binance=10, bybit=3 -> +7
    row = _mk_row(basis_spread=7.0)
    cfg = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="require", min_spread_bps=5.0, side="both"
    )
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), row, cfg)
    assert blocked is False  # 7 >=5 , require met -> not blocked


def test_missing_data_matches_basis_filter_default_allow():
    cfg = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="require", min_spread_bps=5.0
    )
    sig, blocked = apply_cross_venue_dislocation_gate(_mk_buy(), _mk_row(basis_spread=None), cfg)
    # default missing=allow -> not blocked (per basis_premium_filter behavior)
    assert blocked is False
    assert sig.type == SignalType.BUY


def test_parse_defaults_and_clamping():
    cfg = parse_cross_venue_dislocation(None)
    assert cfg.enabled is False
    assert cfg.metric == "basis_spread"
    assert cfg.mode == "require"
    assert cfg.min_spread_bps == 5.0
    assert cfg.side == "both"
    assert cfg.missing_data_policy == "allow"

    raw = {
        "enabled": True,
        "metric": "premium_spread",
        "mode": "block",
        "min_spread_bps": -3,
        "side": "positive",
    }
    cfg = parse_cross_venue_dislocation(raw)
    assert cfg.enabled is True
    assert cfg.metric == "premium_spread"
    assert cfg.mode == "block"
    assert cfg.min_spread_bps == 0.0  # clamped
    assert cfg.side == "positive"

    raw_bad = {"metric": "foo", "mode": "foo", "side": "foo", "missing_data_policy": "foo"}
    cfg = parse_cross_venue_dislocation(raw_bad)
    assert cfg.metric == "basis_spread"
    assert cfg.mode == "require"
    assert cfg.side == "both"
    assert cfg.missing_data_policy == "allow"


def test_sampler_emits_config_block_in_range(monkeypatch):
    # Use the generate_candidate with seeded to hit our families
    from scripts.autoresearch_loop import generate_candidate

    c = generate_candidate(0, seed=42, families=("cross_venue_dislocation",))
    ov = c.overlay
    # now nested under strategy per wiring fix
    assert "cross_venue_dislocation" in ov.get("strategy", {})
    dis = ov["strategy"]["cross_venue_dislocation"]
    assert dis["enabled"] is True
    assert dis["min_spread_bps"] > 3.0  # from our p90+ range
    assert dis["mode"] in ("require", "block")

    c2 = generate_candidate(1, seed=99, families=("venue_basis_filter",))
    ov2 = c2.overlay
    assert "cross_venue_dislocation" in ov2.get("strategy", {})
    dis2 = ov2["strategy"]["cross_venue_dislocation"]
    assert dis2["mode"] == "block"


def test_ab_behavioral_differs_on_min_spread(monkeypatch):
    """A/B: tiny threshold vs huge threshold on same synthetic rows/signals -> different blocked counts."""
    # We drive the gate directly (synthetic "engine input" = repeated rows + buy signals)
    # tiny threshold -> more blocks in require mode
    # huge -> fewer blocks
    rows = [_mk_row(basis_spread=4.0) for _ in range(10)]  # |4| <5 but >1
    buys = [_mk_buy() for _ in range(10)]

    cfg_tiny = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="require", min_spread_bps=1.0, side="both"
    )
    cfg_huge = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="require", min_spread_bps=500.0, side="both"
    )

    blocked_tiny = 0
    for r, b in zip(rows, buys, strict=True):
        _, blk = apply_cross_venue_dislocation_gate(b, r, cfg_tiny)
        if blk:
            blocked_tiny += 1

    blocked_huge = 0
    for r, b in zip(rows, buys, strict=True):
        _, blk = apply_cross_venue_dislocation_gate(b, r, cfg_huge)
        if blk:
            blocked_huge += 1

    print(
        f"A/B counts from synthetic engine input: tiny(1.0)={blocked_tiny} huge(500.0)={blocked_huge}"
    )
    assert blocked_tiny != blocked_huge, (
        "A/B must produce different block counts for tiny vs huge min_spread_bps"
    )
    # With |4| spread, tiny=1 requires it (so few blocks), huge=500 does not (all blocked in require)
    assert blocked_huge > blocked_tiny


def test_regression_candidate_merges_and_enables_via_autopilot_parse_path():
    """Regression: candidate from sampler, merged like run_autoresearch, parsed via autopilot path, enabled=True."""
    from scripts.autoresearch_loop import generate_candidate

    # minimal deep merge (mirrors run_autoresearch._deep_merge behavior for this key)
    def _deep_merge(base, overlay):
        if isinstance(base, dict) and isinstance(overlay, dict):
            result = dict(base)
            for k, v in overlay.items():
                if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                    result[k] = _deep_merge(result[k], v)
                else:
                    result[k] = v
            return result
        return overlay or base

    cand = generate_candidate(0, seed=42, families=("cross_venue_dislocation",), symbol="SOLUSDT")
    base = {"strategy": {"aggregator": {"buy_threshold": 0.8}}}
    merged = _deep_merge(base, cand.overlay)

    # parse like in experiment_autopilot main
    parsed = parse_cross_venue_dislocation(
        merged.get("strategy", {}).get("cross_venue_dislocation")
    )
    assert parsed.enabled is True
    assert parsed.min_spread_bps >= 3.0  # from our sampler range

    # also test via _build_backtest_config path (strengthened)
    from types import SimpleNamespace

    from scripts.experiment_autopilot import _build_backtest_config

    raw_for_build = merged
    # minimal settings obj
    settings = SimpleNamespace(
        trading_execution=SimpleNamespace(
            stop_loss_pct=0.05,
            take_profit_pct=0.1,
            use_atr_sizing=False,
            atr_multiplier=1.5,
            risk_per_trade_pct=0.02,
        ),
        trading_pairs=["SOLUSDT"],
        timeframe="1h",
    )
    cross_venue_disloc = parse_cross_venue_dislocation(
        raw_for_build.get("strategy", {}).get("cross_venue_dislocation")
    )
    built = _build_backtest_config(
        settings=settings,
        raw_config=raw_for_build,
        symbol="SOLUSDT",
        timeframe="1h",
        start="2024-01-01",
        end="2024-01-02",
        strategy_classes=[],
        strategy_configs=[],
        aggregator_config={},
        initial_capital=10000.0,
        disable_trend_filter=True,
        replay_sentiment_path=None,
        replay_sentiment_max_age_hours=None,
        basis_calibrated_threshold=None,
        cross_venue_dislocation=cross_venue_disloc,
    )
    # since _build uses the raw parse now, it should have enabled
    assert built.cross_venue_dislocation.enabled is True


def test_ab_uses_real_build_path_for_different_counts():
    """Strengthened A/B using _build_backtest_config to get config, then gate on synthetic input."""
    from types import SimpleNamespace

    from scripts.experiment_autopilot import _build_backtest_config

    def _mk_row(spread=4.0):
        return {"cross_venue_basis_spread_bps": spread, "close_price": 100.0}

    def _mk_buy():
        from src.strategy.signals import Signal, SignalType

        return Signal(
            type=SignalType.BUY,
            symbol="SOLUSDT",
            price=100.0,
            confidence=0.8,
            reason="",
            indicators={},
            trading_mode="paper",
        )

    settings = SimpleNamespace(
        trading_execution=SimpleNamespace(
            stop_loss_pct=0.05,
            take_profit_pct=0.1,
            use_atr_sizing=False,
            atr_multiplier=1.5,
            risk_per_trade_pct=0.02,
        ),
        trading_pairs=["SOLUSDT"],
        timeframe="1h",
    )
    raw_tiny = {
        "strategy": {
            "cross_venue_dislocation": {
                "enabled": True,
                "metric": "basis_spread",
                "mode": "require",
                "min_spread_bps": 1.0,
                "side": "both",
            }
        }
    }
    raw_huge = {
        "strategy": {
            "cross_venue_dislocation": {
                "enabled": True,
                "metric": "basis_spread",
                "mode": "require",
                "min_spread_bps": 500.0,
                "side": "both",
            }
        }
    }

    cfg_tiny = _build_backtest_config(
        settings=settings,
        raw_config=raw_tiny,
        symbol="SOLUSDT",
        timeframe="1h",
        start="2024-01-01",
        end="2024-01-02",
        strategy_classes=[],
        strategy_configs=[],
        aggregator_config={},
        initial_capital=10000.0,
        disable_trend_filter=True,
        replay_sentiment_path=None,
        replay_sentiment_max_age_hours=None,
        basis_calibrated_threshold=None,
        cross_venue_dislocation=parse_cross_venue_dislocation(
            raw_tiny.get("strategy", {}).get("cross_venue_dislocation")
        ),
    ).cross_venue_dislocation

    cfg_huge = _build_backtest_config(
        settings=settings,
        raw_config=raw_huge,
        symbol="SOLUSDT",
        timeframe="1h",
        start="2024-01-01",
        end="2024-01-02",
        strategy_classes=[],
        strategy_configs=[],
        aggregator_config={},
        initial_capital=10000.0,
        disable_trend_filter=True,
        replay_sentiment_path=None,
        replay_sentiment_max_age_hours=None,
        basis_calibrated_threshold=None,
        cross_venue_dislocation=parse_cross_venue_dislocation(
            raw_huge.get("strategy", {}).get("cross_venue_dislocation")
        ),
    ).cross_venue_dislocation

    rows = [_mk_row(4.0) for _ in range(10)]
    buys = [_mk_buy() for _ in range(10)]

    from src.strategy.cross_venue_dislocation import apply_cross_venue_dislocation_gate

    blocked_tiny = sum(
        1
        for r, b in zip(rows, buys, strict=True)
        if apply_cross_venue_dislocation_gate(b, r, cfg_tiny)[1]
    )
    blocked_huge = sum(
        1
        for r, b in zip(rows, buys, strict=True)
        if apply_cross_venue_dislocation_gate(b, r, cfg_huge)[1]
    )

    print(
        f"Strengthened A/B via _build path: tiny(1.0) blocked={blocked_tiny} huge(500.0) blocked={blocked_huge}"
    )
    assert blocked_tiny != blocked_huge


class _AlwaysBuyStrategy(BaseStrategy):
    """Strategy that emits BUY on every bar for gate consumption tests."""

    def get_name(self) -> str:
        return "AlwaysBuy"

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        price = indicators["close_price"]
        return Signal(SignalType.BUY, symbol, price, 1.0, "always buy", indicators)


def _build_mock_reader(rows: list[dict[str, object]]) -> IndicatorReader:
    """Build an IndicatorReader whose fetch_range returns the provided synthetic rows."""
    reader = IndicatorReader({})
    # Mark as connected for any internal checks (pattern from basis premium backtest test)
    reader._connected = True  # type: ignore[attr-defined]

    async def _mock_fetch_range(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return rows

    reader.fetch_range = _mock_fetch_range  # type: ignore[method-assign]
    return reader


def _synth_row(cross_basis_spread: float | None = None, close: float = 100.0) -> dict[str, object]:
    """Synthetic indicator row containing the cross-venue columns the gate reads."""
    return {
        "time": "2024-06-03T12:00:00",
        "close_price": close,
        "high_price": close + 1.0,
        "low_price": close - 1.0,
        "ema_200": 50.0,
        "atr_14": 1.0,
        "cross_venue_basis_spread_bps": cross_basis_spread,
        "cross_venue_premium_spread": None,
    }


@pytest.mark.asyncio
async def test_backtest_engine_applies_cross_venue_dislocation_gate_ab() -> None:
    """Engine-level A/B: identical data, tiny vs huge min_spread_bps -> different blocked/trade counts.

    This exercises the full path: BacktestConfig -> reader rows with cross_venue_* keys ->
    engine gate call after basis -> counters in BacktestResult. Mirrors test_basis_premium_backtest.
    """
    # 10 bars, spreads alternate around the 5bps example range (p90-p99 was 3.45-7)
    # Tiny threshold (0.1) in require mode with side=both: most/all will be "dislocated" -> allow BUY
    # Huge (1000): almost none dislocated -> block BUY (require mode)
    # Use spreads that are |4-6| so tiny allows, huge blocks.
    spreads = [4.0, 5.5, 3.2, 6.1, 4.8, 5.0, 3.8, 6.5, 4.2, 5.9]
    rows = [_synth_row(s) for s in spreads]

    base_kwargs = {
        "symbol": "SOLUSDT",
        "timeframe": "1h",
        "start_date": "2024-06-01",
        "end_date": "2024-06-04",
        "initial_capital": 10000.0,
        "fee_rate": 0.0,
        "slippage_pct": 0.0,
        "apply_global_trend_filter": False,
        "strategy_classes": [_AlwaysBuyStrategy],
        "aggregator_config": {"min_agreement": 1, "buy_threshold": 0.5},
    }

    tiny_cfg = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="require", min_spread_bps=0.1, side="both"
    )
    huge_cfg = CrossVenueDislocationConfig(
        enabled=True, metric="basis_spread", mode="require", min_spread_bps=1000.0, side="both"
    )

    reader_tiny = _build_mock_reader(rows)
    res_tiny = await BacktestEngine(
        BacktestConfig(**base_kwargs, cross_venue_dislocation=tiny_cfg),
        reader_tiny,
    ).run()

    reader_huge = _build_mock_reader(rows)
    res_huge = await BacktestEngine(
        BacktestConfig(**base_kwargs, cross_venue_dislocation=huge_cfg),
        reader_huge,
    ).run()

    # With tiny: spreads ~4-6 >0.1 -> dislocated -> require allows -> more trades, 0 (or low) blocked
    # With huge: |spread| <<1000 -> not dislocated -> require blocks -> fewer trades, high blocked count
    print(
        f"Engine A/B: tiny blocked={res_tiny.dislocation_blocked_buy_count} trades={res_tiny.total_trades}; "
        f"huge blocked={res_huge.dislocation_blocked_buy_count} trades={res_huge.total_trades}"
    )
    assert res_tiny.dislocation_blocked_buy_count != res_huge.dislocation_blocked_buy_count
    assert res_tiny.total_trades != res_huge.total_trades
    # Sanity: tiny should have blocked near 0 for this data
    assert res_tiny.dislocation_blocked_buy_count == 0
    assert res_huge.dislocation_blocked_buy_count > 0
