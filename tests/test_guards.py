"""Tests for guard pipeline."""


from src.execution.guards import (
    CooldownGuard,
    GuardPipeline,
    GuardResult,
    LeverageGuard,
    LiquidationBufferGuard,
    MaxPositionsGuard,
    PositionSizeGuard,
    create_default_pipeline,
)


class TestPositionSizeGuard:
    def test_pass_under_limit(self):
        guard = PositionSizeGuard(max_position_pct=0.10)
        response = guard.check(
            symbol="BTCUSDT",
            side="BUY",
            quantity=1000,
            portfolio_value=20000,
            context={},
        )
        assert response.result == GuardResult.PASS

    def test_block_over_limit(self):
        guard = PositionSizeGuard(max_position_pct=0.10)
        response = guard.check(
            symbol="BTCUSDT",
            side="BUY",
            quantity=3000,
            portfolio_value=20000,
            context={},
        )
        assert response.result == GuardResult.BLOCK


class TestMaxPositionsGuard:
    def test_pass_under_limit(self):
        guard = MaxPositionsGuard(max_open_positions=5)
        context = {"open_positions": {"BTCUSDT": {}, "ETHUSDT": {}}}
        response = guard.check(
            symbol="SOLUSDT",
            side="BUY",
            quantity=100,
            portfolio_value=10000,
            context=context,
        )
        assert response.result == GuardResult.PASS


class TestCooldownGuard:
    def test_pass_after_cooldown(self, monkeypatch):
        guard = CooldownGuard(cooldown_seconds=60.0)
        guard.record_trade("BTCUSDT")
        monkeypatch.setattr(
            "src.execution.guards.time.monotonic",
            lambda: guard._last_trade_time["BTCUSDT"] + 61.0,
        )
        response = guard.check(
            symbol="BTCUSDT",
            side="BUY",
            quantity=100,
            portfolio_value=10000,
            context={},
        )
        assert response.result == GuardResult.PASS

    def test_block_during_cooldown(self):
        guard = CooldownGuard(cooldown_seconds=60.0)
        guard.record_trade("BTCUSDT")
        response = guard.check(
            symbol="BTCUSDT",
            side="BUY",
            quantity=100,
            portfolio_value=10000,
            context={},
        )
        assert response.result == GuardResult.BLOCK


class TestLeverageGuard:
    def test_pass_under_limit(self):
        guard = LeverageGuard(max_leverage=10)
        context = {"leverage": 5}
        response = guard.check(
            symbol="BTCUSDT",
            side="BUY",
            quantity=100,
            portfolio_value=10000,
            context=context,
        )
        assert response.result == GuardResult.PASS


class TestLiquidationBufferGuard:
    def test_pass_safe_distance(self):
        guard = LiquidationBufferGuard(liquidation_buffer_pct=5.0)
        context = {"liquidation_distance_pct": 10.0}
        response = guard.check(
            symbol="BTCUSDT",
            side="BUY",
            quantity=100,
            portfolio_value=10000,
            context=context,
        )
        assert response.result == GuardResult.PASS


class TestGuardPipeline:
    def test_multiple_guards_all_pass(self):
        pipeline = GuardPipeline(
            guards=[
                PositionSizeGuard(max_position_pct=0.10),
                MaxPositionsGuard(max_open_positions=5),
            ]
        )
        context = {"open_positions": {}}
        is_allowed, responses = pipeline.check(
            symbol="BTCUSDT",
            side="BUY",
            quantity=100,
            portfolio_value=10000,
            context=context,
        )
        assert is_allowed is True
        assert len(responses) == 2


class TestCreateDefaultPipeline:
    def test_create_with_defaults(self):
        pipeline = create_default_pipeline()
        assert len(pipeline.guards) == 5
