"""Tests for dislocation_event standalone strategy + its autoresearch family sampler.

Covers the 6 required behaviors + N=50 sampler validation per Gate 2 spec.
"""

import pytest

from scripts.autoresearch_loop import generate_candidate
from src.strategy.dislocation_event import DislocationEventStrategy
from src.strategy.signals import SignalType


class TestDislocationEventStrategy:
    @pytest.fixture
    def strategy(self) -> DislocationEventStrategy:
        return DislocationEventStrategy({"min_spread_bps": 5.0, "cooldown_bars": 12})

    def _mk_indicators(self, spread: float | None, close: float = 150.0) -> dict[str, float]:
        ind: dict[str, float] = {"close_price": close}
        if spread is not None:
            ind["cross_venue_basis_spread_bps"] = spread  # type: ignore[assignment]
        return ind

    @pytest.mark.asyncio
    async def test_buy_emitted_at_or_above_threshold(
        self, strategy: DislocationEventStrategy
    ) -> None:
        """1. BUY emitted when spread >= threshold (exact boundary included)."""
        # exact boundary
        sig = await strategy.evaluate("SOLUSDT", self._mk_indicators(5.0))
        assert sig.type == SignalType.BUY
        assert sig.symbol == "SOLUSDT"
        assert "cross_venue_dislocation_positive" in sig.reason
        assert sig.confidence > 0.5

        # strictly above (use different symbol to avoid shared cooldown state on same symbol)
        sig2 = await strategy.evaluate("ETHUSDT", self._mk_indicators(6.2))
        assert sig2.type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_no_signal_below_threshold_or_negative(
        self, strategy: DislocationEventStrategy
    ) -> None:
        """2. No signal below threshold; no signal on negative spread of equal magnitude (positive-only)."""
        # below
        sig = await strategy.evaluate("SOLUSDT", self._mk_indicators(4.9))
        assert sig.type == SignalType.HOLD
        assert "below_threshold" in sig.reason

        # negative equal mag
        sig_neg = await strategy.evaluate("SOLUSDT", self._mk_indicators(-5.0))
        assert sig_neg.type == SignalType.HOLD
        assert "below_threshold" in sig_neg.reason

        # larger negative
        sig_neg2 = await strategy.evaluate("SOLUSDT", self._mk_indicators(-7.0))
        assert sig_neg2.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_no_signal_when_spread_missing_or_none(
        self, strategy: DislocationEventStrategy
    ) -> None:
        """3. No signal when cross_venue_basis_spread_bps is None/missing."""
        sig = await strategy.evaluate("SOLUSDT", self._mk_indicators(None))
        assert sig.type == SignalType.HOLD
        assert "missing_cross_venue_basis_spread" in sig.reason

        sig2 = await strategy.evaluate("SOLUSDT", {"close_price": 150.0})
        assert sig2.type == SignalType.HOLD
        assert "missing_cross_venue_basis_spread" in sig2.reason

    @pytest.mark.asyncio
    async def test_cooldown_prevents_refire_within_window(
        self, strategy: DislocationEventStrategy
    ) -> None:
        """4. Cooldown: second qualifying bar within cooldown_bars emits nothing;
        first bar after cooldown expiry emits again.
        """
        cooldown = 12
        # fire first
        sig1 = await strategy.evaluate("SOLUSDT", self._mk_indicators(5.5))
        assert sig1.type == SignalType.BUY

        # within cooldown: no fire even on qualifying
        for _ in range(cooldown - 1):
            sig = await strategy.evaluate("SOLUSDT", self._mk_indicators(6.0))
            assert sig.type == SignalType.HOLD
            assert "cooldown_active" in sig.reason

        # exactly at cooldown boundary: still within? next after the loop is bar offset=11
        # after 11 more calls (bars 2..12), current offset from fire =11, 11 <12 -> hold
        # now one more call reaches bars_since=12
        sig_at_boundary = await strategy.evaluate("SOLUSDT", self._mk_indicators(5.5))
        # with >= logic: after cooldown-1 holds, the next call has bars_since = cooldown , should fire
        assert sig_at_boundary.type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_never_emits_sell(self, strategy: DislocationEventStrategy) -> None:
        """5. Never emits SELL."""
        # qualifying
        sig = await strategy.evaluate("SOLUSDT", self._mk_indicators(7.0))
        assert sig.type != SignalType.SELL
        # sub
        sig2 = await strategy.evaluate("SOLUSDT", self._mk_indicators(3.0))
        assert sig2.type != SignalType.SELL
        # missing
        sig3 = await strategy.evaluate("SOLUSDT", self._mk_indicators(None))
        assert sig3.type != SignalType.SELL
        # negative
        sig4 = await strategy.evaluate("SOLUSDT", self._mk_indicators(-6.0))
        assert sig4.type != SignalType.SELL

    @pytest.mark.asyncio
    async def test_get_name(self, strategy: DislocationEventStrategy) -> None:
        assert strategy.get_name() == "dislocation_event"


def test_dislocation_event_entry_family_sampler_produces_valid_configs() -> None:
    """6. Sampler test: N=50 dislocation_event_entry configs with seeded rng;
    assert ranges, horizon, time_stop==horizon*60, strategies==["dislocation_event"] only.
    Mirrors pattern from test_autoresearch (e.g. volatility_squeeze_bounded loop) and
    test_cross_venue_dislocation sampler tests.
    """
    n = 50
    seen_horizons: set[int] = set()
    for i in range(n):
        cand = generate_candidate(
            i,
            seed=42,
            symbol="SOLUSDT",
            families=("dislocation_event_entry",),
        )
        assert cand.family == "dislocation_event_entry"
        ov = cand.overlay

        # strategies list exactly the one
        strategies = ov["strategy"]["strategies"]
        assert len(strategies) == 1
        assert strategies[0]["name"] == "dislocation_event"
        strat_cfg = strategies[0]["config"]
        min_bps = float(strat_cfg["min_spread_bps"])
        cooldown = int(strat_cfg["cooldown_bars"])

        assert 4.5 <= min_bps <= 7.0
        assert cooldown in (12, 24)

        # time stop
        exit_rules = ov["trading_execution"]["exit_rules"]
        time_stop = float(exit_rules["time_stop_minutes"])
        assert exit_rules["backtest_use_executor_exit_model"] is True
        assert time_stop == cooldown * 60.0

        # also assert fixed wide sl/tp no effective trailing (per design)
        assert ov["trading_execution"]["sl_atr_multiplier"] == 4.0
        assert ov["trading_execution"]["tp_atr_multiplier"] == 8.0

        seen_horizons.add(cooldown)

    # both horizons should be hit over 50 draws
    assert seen_horizons == {12, 24}
