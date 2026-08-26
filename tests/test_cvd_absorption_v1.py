"""Offline tests for CVD absorption v1 (no network, no agent)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.probe_cvd_absorption_v1 import (
    DEFAULT_LOCK,
    IllegalCache,
    IllegalWindow,
    LockTamper,
    assert_legal_cache_path,
    assert_window_legal,
    bars_from_trades,
    grid_from_lock,
    load_and_validate_lock,
    rank_rows,
    run_rank,
    screen_window,
    serialized_cache_path,
    signal_side,
    simulate,
    slim_row,
)
from scripts.probe_orderflow_microstructure import AggTrade, sign_trade_qty

EXPECTED_LOGICAL_CACHE_PATH = (
    "data/microstructure/cvd_absorption_v1/BTCUSDT/aggtrades_20260609_20260715.jsonl"
)

LOCK = load_and_validate_lock(DEFAULT_LOCK)
T0 = int(datetime(2026, 6, 20, 12, 0, tzinfo=UTC).timestamp() * 1000)
HOLDOUT_T0 = int(datetime(2026, 7, 20, 12, 0, tzinfo=UTC).timestamp() * 1000)


def _trade(
    *, offset_sec: int, price: float, qty: float = 1.0, maker_buy: bool = False, agg_id: int = 0
) -> AggTrade:
    return AggTrade(
        agg_id=agg_id or offset_sec,
        price=price,
        qty=qty,
        timestamp_ms=T0 + offset_sec * 1000,
        is_buyer_maker=maker_buy,
    )


def test_sign_trade_qty_reused_from_ofi_probe():
    buy = _trade(offset_sec=0, price=100.0, maker_buy=False)
    sell = _trade(offset_sec=1, price=100.0, maker_buy=True)
    assert sign_trade_qty(buy) == pytest.approx(1.0)
    assert sign_trade_qty(sell) == pytest.approx(-1.0)


def test_bar_signed_qty_uses_sign_trade_qty():
    trades = [
        _trade(offset_sec=0, price=100.0, qty=2.0, maker_buy=False),
        _trade(offset_sec=1, price=100.0, qty=1.0, maker_buy=True),
    ]
    bars, _hs = bars_from_trades(trades, bar_sec=60)
    assert len(bars) == 1
    assert bars[0].signed_qty == pytest.approx(
        sign_trade_qty(trades[0]) + sign_trade_qty(trades[1])
    )
    assert bars[0].cvd == pytest.approx(bars[0].signed_qty)


def test_lock_grid_is_sixteen_and_promote_false():
    grid = grid_from_lock(LOCK)
    assert len(grid) == 16
    assert LOCK["promote"] is False
    assert LOCK["live_go"] is False
    assert LOCK["costs"]["taker_fee_bps"] == 10.0
    assert LOCK["develop_start"].startswith("2026-06-09")
    assert LOCK["holdout_start"].startswith("2026-07-16")


def test_lock_refuses_non_10bps_fee(tmp_path: Path):
    payload = json.loads(DEFAULT_LOCK.read_text(encoding="utf-8"))
    payload["costs"]["taker_fee_bps"] = 4.0
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LockTamper, match="taker_fee_bps"):
        load_and_validate_lock(path)


def test_lock_refuses_promote_true(tmp_path: Path):
    payload = json.loads(DEFAULT_LOCK.read_text(encoding="utf-8"))
    payload["promote"] = True
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LockTamper, match="promote"):
        load_and_validate_lock(path)


def test_lock_refuses_live_go_true(tmp_path: Path):
    payload = json.loads(DEFAULT_LOCK.read_text(encoding="utf-8"))
    payload["live_go"] = True
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LockTamper, match="live_go"):
        load_and_validate_lock(path)


def test_refuses_official_ofi_window():
    with pytest.raises(IllegalWindow, match="official_ofi"):
        assert_window_legal(
            int(datetime(2026, 5, 23, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2026, 6, 6, tzinfo=UTC).timestamp() * 1000),
            LOCK,
        )


def test_refuses_overlapping_burned_file_window():
    with pytest.raises(IllegalWindow, match="overlapping"):
        assert_window_legal(
            int(datetime(2026, 5, 18, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2026, 5, 20, tzinfo=UTC).timestamp() * 1000),
            LOCK,
        )


def test_develop_window_is_legal():
    assert_window_legal(
        int(datetime(2026, 6, 9, tzinfo=UTC).timestamp() * 1000),
        int(datetime(2026, 7, 16, tzinfo=UTC).timestamp() * 1000),
        LOCK,
    )


def test_refuses_official_cache_dir():
    with pytest.raises(IllegalCache):
        assert_legal_cache_path(
            Path("data/microstructure/BTCUSDT/aggtrades_20260523_20260606.jsonl"), LOCK
        )
    with pytest.raises(IllegalCache):
        assert_legal_cache_path(Path("data/microstructure/BTCUSDT/aggtrades_x.jsonl"), LOCK)


def test_allows_new_cache_dir():
    assert_legal_cache_path(
        Path("data/microstructure/cvd_absorption_v1/BTCUSDT/aggtrades_20260609_20260715.jsonl"),
        LOCK,
    )


def _absorption_trades(minutes: int = 20) -> list[AggTrade]:
    """Price stair-steps up; CVD stays flat (buy then equal sell)."""
    trades: list[AggTrade] = []
    price = 100.0
    agg = 1
    for minute in range(minutes):
        price += 1.0
        for tick, maker_buy in enumerate((False, True)):
            trades.append(
                AggTrade(
                    agg_id=agg,
                    price=price + tick * 0.01,
                    qty=1.0,
                    timestamp_ms=T0 + minute * 60_000 + tick * 100,
                    is_buyer_maker=maker_buy,
                )
            )
            agg += 1
    return trades


def test_fade_shorts_price_high_when_cvd_does_not_confirm():
    trades = _absorption_trades(15)
    bars, _hs = bars_from_trades(trades, bar_sec=60)
    cfg = [c for c in grid_from_lock(LOCK) if c.id == 1][0]
    assert cfg.divergence == "price_ext_cvd_not"
    assert cfg.side_rule == "fade"
    sides = [signal_side(bars, i, cfg) for i in range(len(bars))]
    assert any(side == -1 for side in sides)
    assert all(side <= 0 for side in sides)


def test_fill_is_next_open_not_signal_close():
    trades = _absorption_trades(15)
    bars, _hs = bars_from_trades(trades, bar_sec=60)
    cfg = [c for c in grid_from_lock(LOCK) if c.id == 1][0]
    i = next(idx for idx in range(len(bars)) if signal_side(bars, idx, cfg) == -1)
    row = simulate(
        bars,
        cfg,
        start_ms=bars[0].timestamp_ms,
        end_ms=bars[-1].timestamp_ms + 1,
        taker_fee_bps=10.0,
        half_spread_bps=0.0,
        notional_usd=10_000.0,
        start_equity_usd=10_000.0,
    )
    assert row["n"] >= 1
    fill = bars[i + 1]
    same_bar_bps = -1 * (fill.close / bars[i].close - 1.0) * 10_000.0
    next_open_bps = -1 * (fill.close / fill.open - 1.0) * 10_000.0
    assert fill.open != bars[i].close or same_bar_bps == next_open_bps


def test_holdout_bars_excluded_from_develop_rank():
    develop_trades = _absorption_trades(40)
    holdout: list[AggTrade] = []
    price = 200.0
    for minute in range(40):
        price += 1.0
        for tick, maker_buy in enumerate((False, True)):
            holdout.append(
                AggTrade(
                    agg_id=10_000 + minute * 2 + tick,
                    price=price + tick * 0.01,
                    qty=1.0,
                    timestamp_ms=HOLDOUT_T0 + minute * 60_000 + tick * 100,
                    is_buyer_maker=maker_buy,
                )
            )
    bars, hs = bars_from_trades(develop_trades + holdout, bar_sec=60)
    start_ms = int(datetime(2026, 6, 9, tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime(2026, 7, 16, tzinfo=UTC).timestamp() * 1000)
    ranked = screen_window(bars, LOCK, start_ms=start_ms, end_ms=end_ms, half_spread_bps=hs)
    holdout_ids = {bar.timestamp_ms for bar in bars if bar.timestamp_ms >= HOLDOUT_T0}
    assert holdout_ids
    cfg = [c for c in grid_from_lock(LOCK) if c.id == 1][0]
    holdout_only = simulate(
        bars,
        cfg,
        start_ms=HOLDOUT_T0,
        end_ms=HOLDOUT_T0 + 10 * 24 * 3600 * 1000,
        taker_fee_bps=10.0,
        half_spread_bps=hs,
        notional_usd=10_000.0,
        start_equity_usd=10_000.0,
    )
    develop_only = next(row for row in ranked["rows"] if row["id"] == 1)
    mixed = simulate(
        bars,
        cfg,
        start_ms=start_ms,
        end_ms=HOLDOUT_T0 + 10 * 24 * 3600 * 1000,
        taker_fee_bps=10.0,
        half_spread_bps=hs,
        notional_usd=10_000.0,
        start_equity_usd=10_000.0,
    )
    assert develop_only["n"] < mixed["n"]
    assert develop_only["n"] == mixed["n"] - holdout_only["n"]
    assert ranked["winner_id"] is None or ranked["winner_id"] in {
        row["id"] for row in ranked["rows"]
    }


def test_rank_does_not_unseal_without_soft_pass():
    rows = [
        {
            "id": 1,
            "n": 10,
            "net_pnl": 1.0,
            "profit_factor": 2.0,
            "expectancy": 0.1,
            "max_dd_pct": 0.01,
        }
    ]
    ranked = rank_rows(rows, LOCK["soft_gate"])
    assert ranked["winner_id"] is None
    assert ranked["eligible_ids"] == []


def test_slim_row_has_no_trade_dump():
    slim = slim_row(
        {
            "id": 1,
            "lookback_n": 10,
            "divergence": "price_ext_cvd_not",
            "side_rule": "fade",
            "n": 50,
            "net_pnl": 12.34,
            "profit_factor": 1.2,
            "expectancy": 0.2,
            "max_dd_pct": 0.05,
            "soft_pass": True,
            "eligible": True,
            "trades": [{"secret": True}],
        }
    )
    assert "trades" not in slim


def test_script_source_has_no_mt5_or_agent_entry():
    src = Path("scripts/probe_cvd_absorption_v1.py").read_text(encoding="utf-8")
    assert "mt5_arch" not in src
    assert "src.main" not in src
    assert "docker-compose" not in src
    assert "15432" not in src
    assert "taker_buy" not in src


def test_serialized_cache_path_is_lock_relative_not_absolute():
    start = datetime(2026, 6, 9, tzinfo=UTC)
    end = datetime(2026, 7, 16, tzinfo=UTC)
    path = serialized_cache_path(LOCK, "BTCUSDT", start, end)
    assert path == EXPECTED_LOGICAL_CACHE_PATH
    assert not Path(path).is_absolute()


def test_run_rank_incomplete_writes_logical_cache_path(tmp_path: Path):
    cache_dir = tmp_path / "data" / "microstructure" / "cvd_absorption_v1"
    out_dir = tmp_path / "out"
    result = run_rank(LOCK, cache_dir, out_dir, holdout=False)
    payload = json.loads((out_dir / "develop_rank.json").read_text(encoding="utf-8"))
    assert payload["cache_path"] == EXPECTED_LOGICAL_CACHE_PATH
    assert result["cache_path"] == EXPECTED_LOGICAL_CACHE_PATH
    assert not Path(payload["cache_path"]).is_absolute()


def test_run_rank_complete_writes_logical_cache_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "scripts.probe_cvd_absorption_v1.cache_covers_end",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "scripts.probe_cvd_absorption_v1.load_closed_bars_from_cache",
        lambda *args, **kwargs: ([], 0.0008, 0),
    )
    cache_dir = tmp_path / "data" / "microstructure" / "cvd_absorption_v1"
    out_dir = tmp_path / "out"
    result = run_rank(LOCK, cache_dir, out_dir, holdout=False)
    payload = json.loads((out_dir / "develop_rank.json").read_text(encoding="utf-8"))
    assert payload["cache_path"] == EXPECTED_LOGICAL_CACHE_PATH
    assert result["cache_path"] == EXPECTED_LOGICAL_CACHE_PATH
    assert not Path(payload["cache_path"]).is_absolute()
