"""Tests for the Polymarket calibration probe.

Covers market filtering, calibration bucketing, edge math, binomial gate, and verdict
routing on injected fixtures. No network or DB.
"""

from datetime import UTC, datetime, timedelta

import pytest

from scripts.probe_polymarket_calibration import (
    BLOCKED_ON_DATA,
    HAS_PULSE,
    NO_PULSE,
    PULL_INCOMPLETE,
    WEAK_EDGE,
    DataAudit,
    LeadTimeResult,
    MarketObservation,
    ProbeConfig,
    PullCompleteness,
    ResolvedMarket,
    analyze_lead_time,
    assign_bucket,
    build_observations,
    classify_raw_market,
    compute_calibration_buckets,
    decide_verdict,
    filter_for_liquidity,
    is_disputed_market,
    price_at_lead,
    two_sided_binom_pvalue,
)

T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


def _market(
    *,
    market_id: str = "m1",
    closed: datetime = T0,
    outcome_yes: int = 0,
    category_group: str = "politics",
    volume: float = 5000.0,
) -> ResolvedMarket:
    return ResolvedMarket(
        market_id=market_id,
        question=f"Test market {market_id}",
        condition_id=f"cond-{market_id}",
        yes_token_id=f"tok-{market_id}",
        category=category_group,
        category_group=category_group,
        volume_usd=volume,
        closed_time=closed,
        outcome_yes=outcome_yes,
        disputed_flag=False,
        slug=f"test-{market_id}",
    )


def _obs(
    market: ResolvedMarket,
    price: float,
    lead_hours: int = 24,
) -> MarketObservation:
    return MarketObservation(market=market, lead_hours=lead_hours, price_at_tau=price)


def _gate_config(**overrides) -> ProbeConfig:
    defaults = {
        "start": "2024-12-20T00:00:00Z",
        "end": "2026-06-20T00:00:00Z",
        "lead_hours": (24, 72),
        "buckets": 10,
        "min_markets": 300,
        "round_trip_cost_pct": 2.5,
        "min_liquidity": 1000.0,
        "cache_file": None,
        "max_price_fetch": None,
        "refresh_cache": False,
    }
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def _complete_pull() -> PullCompleteness:
    return PullCompleteness(
        pages_fetched=10,
        pagination_mode="offset+date_window",
        termination="complete",
        error_detail=None,
        earliest_end_date="2024-01-01T00:00:00+00:00",
        latest_end_date="2026-06-20T00:00:00+00:00",
        incomplete=False,
    )


def _audit(**overrides) -> DataAudit:
    defaults = {
        "total_pulled": 100,
        "exclusions": {},
        "disputed_flagged": 0,
        "with_price_by_lead": {"24h": 350, "72h": 221},
        "usable_for_edge": 350,
        "category_mix": {"politics": 50, "sports": 50},
        "pull_completeness": _complete_pull(),
        "blocked": False,
        "blocked_reason": None,
    }
    defaults.update(overrides)
    return DataAudit(**defaults)


def test_classify_rejects_invalid_refunded_and_unresolved():
    refunded = {
        "id": "1",
        "question": "Refund?",
        "umaResolutionStatus": "resolved",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.5","0.5"]',
        "clobTokenIds": '["111","222"]',
        "closedTime": "2025-01-01 00:00:00+00",
        "volumeNum": 5000,
    }
    market, reason = classify_raw_market(refunded)
    assert market is None
    assert reason == "invalid_refunded"

    proposed = {**refunded, "outcomePrices": '["1","0"]', "umaResolutionStatus": "proposed"}
    market, reason = classify_raw_market(proposed)
    assert market is None
    assert reason == "unresolved"


def test_classify_keeps_clean_binary_resolved():
    raw = {
        "id": "42",
        "question": "Will BTC close above 50k?",
        "umaResolutionStatus": "resolved",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0","1"]',
        "clobTokenIds": '["111","222"]',
        "closedTime": "2025-06-01 00:00:00+00",
        "volumeNum": 25000,
        "category": "crypto",
        "conditionId": "0xabc",
        "slug": "btc-50k",
    }
    market, reason = classify_raw_market(raw)
    assert reason == ""
    assert market is not None
    assert market.outcome_yes == 0
    assert market.category_group == "crypto"


def test_filter_low_liquidity_and_disputed():
    illiquid = _market(volume=50.0)
    assert filter_for_liquidity(illiquid, 1000.0) == "low_liquidity"
    assert is_disputed_market("Iran strike resolution disputed by oracle")


def test_assign_bucket_deciles():
    assert assign_bucket(0.05, 10) == 0
    assert assign_bucket(0.95, 10) == 9
    assert assign_bucket(0.25, 10) == 2


def test_price_at_lead_no_leakage():
    close_ts = int(T0.timestamp())
    history = [
        (close_ts - 96 * 3600, 0.20),
        (close_ts - 72 * 3600, 0.30),
        (close_ts - 48 * 3600, 0.90),  # after τ=72h target — must not be used
    ]
    price = price_at_lead(history, close_ts, lead_hours=72)
    assert price == pytest.approx(0.30)


def test_calibration_longshot_overpricing_edge():
    """Hand-built: low-price bucket overpriced => positive edge, high bucket underpriced."""
    markets: list[MarketObservation] = []
    for i in range(20):
        markets.append(_obs(_market(market_id=f"lo-{i}", outcome_yes=0), price=0.08))
    for i in range(20):
        markets.append(_obs(_market(market_id=f"hi-{i}", outcome_yes=1), price=0.92))

    buckets = compute_calibration_buckets(markets, buckets=10, round_trip_cost_pct=0.5)
    low = buckets[0]
    high = buckets[9]
    assert low.n == 20
    assert low.mean_price == pytest.approx(0.08)
    assert low.realized_freq == pytest.approx(0.0)
    assert low.edge == pytest.approx(0.08)
    assert high.realized_freq == pytest.approx(1.0)
    assert high.edge == pytest.approx(-0.08)


def test_binomial_significance_gate():
    # 20 trials, p0=0.5, 20 successes => very small p
    p = two_sided_binom_pvalue(20, 20, 0.5)
    assert p < 0.001
    # Perfect calibration => high p
    p_flat = two_sided_binom_pvalue(5, 10, 0.5)
    assert p_flat > 0.5


def test_analyze_has_pulse_multi_category(tmp_path):
    """Synthetic edge in longshots across politics + sports survives H2."""
    observations: list[MarketObservation] = []
    for i in range(60):
        day = i * 4
        observations.append(
            _obs(
                _market(
                    market_id=f"lo-pol-{i}",
                    closed=T0 + timedelta(days=day),
                    outcome_yes=0,
                    category_group="politics",
                ),
                price=0.12,
            )
        )
        observations.append(
            _obs(
                _market(
                    market_id=f"lo-sport-{i}",
                    closed=T0 + timedelta(days=day + 1),
                    outcome_yes=0,
                    category_group="sports",
                ),
                price=0.12,
            )
        )
        observations.append(
            _obs(
                _market(
                    market_id=f"hi-sport-{i}",
                    closed=T0 + timedelta(days=day + 2),
                    outcome_yes=1,
                    category_group="sports",
                ),
                price=0.88,
            )
        )
        observations.append(
            _obs(
                _market(
                    market_id=f"hi-crypto-{i}",
                    closed=T0 + timedelta(days=day + 3),
                    outcome_yes=1,
                    category_group="crypto",
                ),
                price=0.88,
            )
        )

    config = _gate_config(round_trip_cost_pct=0.5, buckets=10)
    result = analyze_lead_time(observations, lead_hours=24, config=config)
    assert result.h1_pass
    assert result.h2_time_split_pass
    assert result.h2_category_exclusion_pass
    assert not result.single_category_only

    audit = _audit(
        total_pulled=240,
        with_price_by_lead={"24h": 240},
        usable_for_edge=240,
        category_mix={"politics": 60, "sports": 120, "crypto": 60},
    )
    status, verdict, _ = decide_verdict(audit, (result,))
    assert status == "OK"
    assert verdict == HAS_PULSE


def test_analyze_weak_edge_single_category():
    observations = [
        _obs(_market(market_id=f"p-{i}", outcome_yes=0, category_group="politics"), price=0.15)
        for i in range(50)
    ]
    config = _gate_config(round_trip_cost_pct=0.5)
    result = analyze_lead_time(observations, lead_hours=24, config=config)
    assert result.h1_pass
    assert result.single_category_only

    audit = _audit(
        total_pulled=50,
        with_price_by_lead={"24h": 50},
        usable_for_edge=50,
        category_mix={"politics": 50},
    )
    _, verdict, _ = decide_verdict(audit, (result,))
    assert verdict == WEAK_EDGE


def test_analyze_no_pulse_inside_cost_noise():
    observations = []
    for i in range(50):
        yes = 1 if i % 2 == 0 else 0
        price = 0.50
        observations.append(_obs(_market(market_id=f"n-{i}", outcome_yes=yes), price=price))

    config = _gate_config(round_trip_cost_pct=5.0)
    result = analyze_lead_time(observations, lead_hours=24, config=config)
    assert not result.h1_pass

    audit = _audit(
        total_pulled=50,
        with_price_by_lead={"24h": 50},
        usable_for_edge=50,
        category_mix={"politics": 50},
    )
    _, verdict, _ = decide_verdict(audit, (result,))
    assert verdict == NO_PULSE


def test_decide_pull_incomplete():
    audit = _audit(
        pull_completeness=PullCompleteness(
            pages_fetched=21,
            pagination_mode="offset",
            termination="error",
            error_detail="offset=2100: ClientResponseError",
            earliest_end_date="2024-06-01T00:00:00+00:00",
            latest_end_date="2026-06-20T00:00:00+00:00",
            incomplete=True,
        )
    )
    status, verdict, _ = decide_verdict(audit, ())
    assert status == PULL_INCOMPLETE
    assert verdict == PULL_INCOMPLETE


def test_decide_blocked_on_data():
    audit = _audit(
        total_pulled=50,
        exclusions={"low_liquidity": 50},
        with_price_by_lead={"24h": 50, "72h": 40},
        usable_for_edge=50,
        category_mix={},
        blocked=True,
        blocked_reason="too few",
    )
    status, verdict, reasons = decide_verdict(audit, ())
    assert status == BLOCKED_ON_DATA
    assert verdict == BLOCKED_ON_DATA
    assert reasons[0] == "too few"


def test_build_observations_respects_price_map():
    markets = [_market(market_id="a"), _market(market_id="b")]
    prices = {("a", 24): 0.4}
    obs = build_observations(markets, prices, lead_hours=24)
    assert len(obs) == 1
    assert obs[0].market.market_id == "a"


def test_decide_verdict_ignores_insufficient_tau():
    good = LeadTimeResult(
        lead_hours=24,
        observations=350,
        data_sufficient=True,
        insufficient_note=None,
        buckets=(),
        qualifying_bucket_indices=(0,),
        h1_pass=True,
        h2_time_split_pass=True,
        h2_category_exclusion_pass=True,
        h2_pass=True,
        single_category_only=False,
        dominant_category="politics",
    )
    thin = LeadTimeResult(
        lead_hours=72,
        observations=221,
        data_sufficient=False,
        insufficient_note="only 221 markets with price at τ (need >= 300) — insufficient for this τ",
        buckets=(),
        qualifying_bucket_indices=(),
        h1_pass=False,
        h2_time_split_pass=False,
        h2_category_exclusion_pass=False,
        h2_pass=False,
        single_category_only=False,
        dominant_category=None,
    )
    audit = _audit(with_price_by_lead={"24h": 350, "72h": 221}, usable_for_edge=350)
    _, verdict, reasons = decide_verdict(audit, (good, thin))
    assert verdict == HAS_PULSE
    assert any("insufficient" in r.lower() for r in reasons)
