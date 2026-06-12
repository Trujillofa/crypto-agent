"""Tests for dislocation_event rolling-threshold mode + dislocation_event_rolling_entry autoresearch family.

All 7 required test cases per Gate 2 spec (cross-venue-dislocation-event-v1).
Existing v0 fixed-mode tests (test_dislocation_event_strategy.py) must pass unmodified.
"""

import random
from datetime import UTC, datetime, timedelta

import pytest

from scripts.autoresearch_loop import generate_candidate
from scripts.probe_basis_premium import tail_threshold_high as probe_tail_threshold_high
from src.strategy.dislocation_event import (
    DislocationEventStrategy,
)
from src.strategy.dislocation_event import (
    tail_threshold_high as strategy_tail_threshold_high,
)
from src.strategy.signals import SignalType

T0 = datetime(2024, 1, 1, tzinfo=UTC)


class TestDislocationEventRolling:
    def _mk_indicators(
        self, spread: float, ts: datetime, close: float = 150.0, use_premium: bool = False
    ) -> dict[str, object]:
        key = "cross_venue_premium_spread" if use_premium else "cross_venue_basis_spread_bps"
        return {"close_price": close, "time": ts, key: spread}

    @pytest.fixture
    def rolling_strategy(self) -> DislocationEventStrategy:
        return DislocationEventStrategy(
            {
                "threshold_mode": "rolling",
                "metric": "basis_spread",
                "tail_pct": 5,
                "rolling_days": 90,
                "cooldown_bars": 12,
            }
        )

    def test_percentile_parity(self) -> None:
        """1. Percentile parity: strategy's tail_threshold_high exactly equals probe's on random arrays."""
        rng = random.Random(12345)
        for _ in range(30):
            n = rng.randint(2, 200)
            arr = [rng.uniform(-50.0, 200.0) for _ in range(n)]
            for tp in (1, 5, 10, 25, 50):
                s = strategy_tail_threshold_high(arr, tp)
                p = probe_tail_threshold_high(arr, tp)
                assert s == p, f"mismatch n={n} tail={tp}: {s} != {p}"

    @pytest.mark.asyncio
    async def test_no_lookahead_future_poison_unaffects_threshold_at_i(
        self, rolling_strategy: DislocationEventStrategy
    ) -> None:
        """2. No-lookahead: plant future poison; th/decision at bar i unaffected by values at >=i.

        Mirrors probe test construction (test_rolling_threshold_at_i_ignores_bars_ge_i) but
        via sequential evaluate() calls (the production path) with append-after-eval.
        """
        sym = "SOLUSDT"
        # Warmup with *strictly decreasing* low values: ensures fill bars < high-tail th of priors
        # (prevents accidental BUY on fillers; th remains ~0.1x from the higher-in-window early fillers)
        for i in range(5):
            ts = T0 + timedelta(hours=i)
            filler = 0.100 - (i * 0.001)
            await rolling_strategy.evaluate(sym, self._mk_indicators(filler, ts))

        # Snapshot priors before bar i (these are < i)
        prior_sv = list(rolling_strategy._sorted_vals.get(sym, []))
        assert len(prior_sv) >= 2
        th_prior = strategy_tail_threshold_high(prior_sv, 5)
        th_prior_internal = rolling_strategy._tail_threshold_high_sorted(prior_sv, 5)
        assert th_prior == th_prior_internal
        assert th_prior < 1.0

        # val_i between prior-only th and (hypothetical) future-poison th
        val_i = th_prior + 0.05
        ind_i = self._mk_indicators(val_i, T0 + timedelta(hours=5))
        sig_i = await rolling_strategy.evaluate(sym, ind_i)
        assert sig_i.type == SignalType.BUY, (
            "fired using low prior th; future poison not yet appended"
        )

        # Plant future poison AFTER i's decision
        for j in range(1, 5):
            ts = T0 + timedelta(hours=5 + j)
            await rolling_strategy.evaluate(sym, self._mk_indicators(999.0, ts))

        # i's decision is already committed without seeing >=i poisons (verified by the BUY)

    @pytest.mark.asyncio
    async def test_warmup_holds_until_2_prior_values(
        self, rolling_strategy: DislocationEventStrategy
    ) -> None:
        """3. Warm-up: <2 prior values in window -> HOLD with rolling_warmup reason."""
        sym = "SOLUSDT"
        # 0 priors
        sig0 = await rolling_strategy.evaluate(sym, self._mk_indicators(100.0, T0))
        assert sig0.type == SignalType.HOLD
        assert sig0.reason == "rolling_warmup"

        # 1 prior
        sig1 = await rolling_strategy.evaluate(
            sym, self._mk_indicators(100.0, T0 + timedelta(hours=1))
        )
        assert sig1.type == SignalType.HOLD
        assert sig1.reason == "rolling_warmup"

        # 2 priors: th of [100,100] tail5 ==100; 100>= fires (new symbol state, cd irrelevant)
        sig2 = await rolling_strategy.evaluate(
            sym, self._mk_indicators(100.0, T0 + timedelta(hours=2))
        )
        assert sig2.type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_window_expiry_drops_stale_and_lowers_th(self) -> None:
        """4. Window expiry: stale extreme older than rolling_days drops, th falls; bar that
        would not fire under stale-inclusive window now fires.
        """
        strat = DislocationEventStrategy(
            {
                "threshold_mode": "rolling",
                "metric": "basis_spread",
                "tail_pct": 5,
                "rolling_days": 1,
                "cooldown_bars": 100,  # large cd so time-based expiry test not interfered by cd
            }
        )
        sym = "SOLUSDT"
        # t0 poison extreme
        await strat.evaluate(sym, self._mk_indicators(1000.0, T0))
        # Fill >1 day of *decreasing* low values (poison drops on cutoff; fillers < th of priors)
        for i in range(1, 30):
            ts = T0 + timedelta(hours=i)
            filler = 0.100 - (i * 0.0005)
            await strat.evaluate(sym, self._mk_indicators(filler, ts))

        # Demonstrate a bar now fires post-expiry (th dropped); would have been below while stale in
        ts_late = T0 + timedelta(hours=30)
        sig = await strat.evaluate(sym, self._mk_indicators(0.5, ts_late))
        assert sig.type == SignalType.BUY
        assert "rolling" in sig.reason

    @pytest.mark.asyncio
    async def test_rolling_conf_075_fixed_conf_unchanged(self) -> None:
        """5. Rolling-mode confidence exactly 0.75; fixed-mode behavior unchanged (v0 regression)."""
        # rolling
        rs = DislocationEventStrategy(
            {"threshold_mode": "rolling", "tail_pct": 5, "rolling_days": 90, "cooldown_bars": 1}
        )
        for k in range(3):
            filler = 0.010 - (k * 0.0001)
            await rs.evaluate("S", self._mk_indicators(filler, T0 + timedelta(hours=k)))
        sig_r = await rs.evaluate("S", self._mk_indicators(10.0, T0 + timedelta(hours=3)))
        assert sig_r.type == SignalType.BUY
        assert sig_r.confidence == 0.75

        # fixed (default config, v0 path)
        fs = DislocationEventStrategy({"min_spread_bps": 5.0, "cooldown_bars": 1})
        ind_f = {"close_price": 100.0, "time": T0, "cross_venue_basis_spread_bps": 15.0}
        sig_f = await fs.evaluate("S2", ind_f)
        assert sig_f.type == SignalType.BUY
        # v0: min(0.6 + (15-5)/5, 1) == 1.0
        assert abs(sig_f.confidence - 1.0) < 1e-9

    @pytest.mark.asyncio
    async def test_cooldown_works_in_rolling(
        self, rolling_strategy: DislocationEventStrategy
    ) -> None:
        """6. Cooldown works in rolling mode (same pattern as v0 test)."""
        sym = "SOLUSDT"
        cd = 12
        # warmup with strictly decreasing lows (fillers < th of their priors; no accidental fires)
        for k in range(5):
            filler = 0.100 - (k * 0.001)
            await rolling_strategy.evaluate(
                sym, self._mk_indicators(filler, T0 + timedelta(hours=k))
            )
        # fire 1.0 (>= low th from the decreasing priors)
        sig1 = await rolling_strategy.evaluate(
            sym, self._mk_indicators(1.0, T0 + timedelta(hours=5))
        )
        assert sig1.type == SignalType.BUY

        # within cd: feed 2.0 (>= th now including prior 1.0 from fire) -> cd blocks
        for _ in range(cd - 1):
            sig = await rolling_strategy.evaluate(
                sym, self._mk_indicators(2.0, T0 + timedelta(hours=6))
            )
            assert sig.type == SignalType.HOLD
            assert "cooldown_active" in sig.reason

        # after cd
        sig_after = await rolling_strategy.evaluate(
            sym, self._mk_indicators(2.0, T0 + timedelta(hours=5 + cd))
        )
        assert sig_after.type == SignalType.BUY

    def test_rolling_entry_family_sampler_produces_valid_configs(self) -> None:
        """7. Sampler property test N=50 seeded: family, domains, ties, rolling_mode, basis->tail5,
        time_stop==horizon*60, sl/tp/trailing neutral, desc.
        """
        n = 50
        seen_horizons: set[int] = set()
        seen_metrics: set[str] = set()
        seen_tails_premium: set[int] = set()
        for i in range(n):
            cand = generate_candidate(
                i,
                seed=42,
                symbol="SOLUSDT",
                families=("dislocation_event_rolling_entry",),
            )
            assert cand.family == "dislocation_event_rolling_entry"
            ov = cand.overlay

            strategies = ov["strategy"]["strategies"]
            assert len(strategies) == 1
            assert strategies[0]["name"] == "dislocation_event"
            cfg = strategies[0]["config"]

            assert cfg["threshold_mode"] == "rolling"
            assert cfg["rolling_days"] == 90
            metric = str(cfg["metric"])
            tail = int(cfg["tail_pct"])
            horizon = int(cfg["cooldown_bars"])
            assert horizon in (12, 24)
            assert cfg["cooldown_bars"] == horizon

            seen_metrics.add(metric)
            if metric == "basis_spread":
                assert tail == 5
            else:
                assert metric == "premium_spread"
                assert tail in (5, 10)
                seen_tails_premium.add(tail)

            exit_rules = ov["trading_execution"]["exit_rules"]
            time_stop = float(exit_rules["time_stop_minutes"])
            assert exit_rules["backtest_use_executor_exit_model"] is True
            assert time_stop == float(horizon * 60)

            assert ov["trading_execution"]["sl_atr_multiplier"] == 4.0
            assert ov["trading_execution"]["tp_atr_multiplier"] == 8.0
            ta = float(ov["trading_execution"]["trailing_activate_atr"])
            assert 8.0 <= ta <= 12.0
            assert ov["trading_execution"]["trailing_offset_atr"] == 0.5

            desc = cand.description
            assert "dislocation-event-rolling-entry" in desc
            assert f"metric={metric}" in desc
            assert f"tail={tail}" in desc
            assert f"horizon={horizon}" in desc

            seen_horizons.add(horizon)

        assert seen_horizons == {12, 24}
        assert seen_metrics == {"basis_spread", "premium_spread"}
        assert seen_tails_premium == {5, 10}
