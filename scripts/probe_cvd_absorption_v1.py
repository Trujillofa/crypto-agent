#!/usr/bin/env python3
"""Read-only Binance spot CVD absorption screen (research only).

Thesis (locked): closed 1m bar — price makes an N-bar extreme while CVD does not
(or reverse) → fade/follow next bar open, after 10 bps/side + tape half-spread.

Not the closed OFI z-score → +h return probe. Not the agent. No live orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_orderflow_microstructure import AggTrade, parse_agg_trade, sign_trade_qty

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = REPO_ROOT / "research/rbi_loop/cvd-absorption-v1/lock.json"
DEFAULT_OUT_DIR = REPO_ROOT / "research/rbi_loop/cvd-absorption-v1"
AGG_TRADES_URL = "https://api.binance.com/api/v3/aggTrades"
AGG_LIMIT = 1000
MAX_RETRIES = 8
RETRY_BASE_SEC = 1.0
REQUEST_DELAY_SEC = 0.20
CACHE_FLUSH_EVERY = 100_000
BANNED_STEMS = ("20260523_20260606", "20260518_20260608")
LOG = logging.getLogger("cvd_absorption_v1")


class LockTamper(ValueError):
    """Lock was edited after freeze in a way this script must refuse."""


class IllegalWindow(ValueError):
    """Requested tape window overlaps a sealed / burned OFI interval."""


class IllegalCache(ValueError):
    """Cache path would touch official 14d files or the wrong directory."""


@dataclass(frozen=True)
class GridConfig:
    id: int
    lookback_n: int
    divergence: str
    side_rule: str
    bar_sec: int
    hold_bars: int


@dataclass(frozen=True)
class ClosedBar:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    signed_qty: float
    qty_total: float
    cvd: float


@dataclass
class _BarAcc:
    open: float
    high: float
    low: float
    close: float
    signed_qty: float = 0.0
    qty_total: float = 0.0

    def add(self, price: float, qty: float, signed: float) -> None:
        self.close = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.signed_qty += signed
        self.qty_total += qty

    def to_bar(self, timestamp_ms: int, cvd: float) -> ClosedBar:
        return ClosedBar(
            timestamp_ms=timestamp_ms,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            signed_qty=self.signed_qty,
            qty_total=self.qty_total,
            cvd=cvd,
        )


def _parse_dt(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _ms(raw: str) -> int:
    return int(_parse_dt(raw).timestamp() * 1000)


def load_and_validate_lock(path: Path) -> dict:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("promote") is True:
        raise LockTamper("promote=true refused")
    if lock.get("live_go") is True:
        raise LockTamper("live_go=true refused")
    fee = float(lock["costs"]["taker_fee_bps"])
    if abs(fee - 10.0) > 1e-12:
        raise LockTamper(f"taker_fee_bps={fee} ≠ 10 refused")
    grid = lock.get("grid") or []
    if len(grid) != 16 or int(lock.get("n_configs_expected", 0)) != 16:
        raise LockTamper("grid must be exactly 16 configs, frozen before metrics")
    if lock.get("symbol") != "BTCUSDT":
        raise LockTamper("symbol must stay BTCUSDT")
    return lock


def grid_from_lock(lock: dict) -> tuple[GridConfig, ...]:
    return tuple(
        GridConfig(
            id=int(row["id"]),
            lookback_n=int(row["lookback_n"]),
            divergence=str(row["divergence"]),
            side_rule=str(row["side_rule"]),
            bar_sec=int(row["bar_sec"]),
            hold_bars=int(row["hold_bars"]),
        )
        for row in lock["grid"]
    )


def intervals_overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def assert_window_legal(start_ms: int, end_ms: int, lock: dict) -> None:
    for banned in lock["forbidden_selection_windows"]:
        b0 = _ms(banned["start"])
        b1 = _ms(banned["end_exclusive"])
        if intervals_overlap(start_ms, end_ms, b0, b1):
            raise IllegalWindow(
                f"window overlaps forbidden {banned['label']} "
                f"({banned['start']} → {banned['end_exclusive']})"
            )


def assert_legal_cache_path(path: Path, lock: dict) -> None:
    text = str(path)
    if "cvd_absorption_v1" not in Path(path).parts and "cvd_absorption_v1" not in text:
        raise IllegalCache(f"cache must live under {lock['cache_dir']}: {path}")
    for stem in BANNED_STEMS:
        if stem in path.name:
            raise IllegalCache(f"official/burned OFI file refused: {path}")
    for banned in lock["forbidden_cache_paths"]:
        if text.rstrip("/") == banned.rstrip("/") or text.startswith(banned):
            if "cvd_absorption_v1" not in banned:
                raise IllegalCache(f"official microstructure path refused: {path}")


def cache_path_for(cache_dir: Path, symbol: str, start: datetime, end_exclusive: datetime) -> Path:
    last = datetime.fromtimestamp((end_exclusive.timestamp() - 1), tz=UTC)
    label = f"{start.strftime('%Y%m%d')}_{last.strftime('%Y%m%d')}"
    return cache_dir / symbol / f"aggtrades_{label}.jsonl"


def read_last_jsonl_record(path: Path) -> dict | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 16_384))
        chunk = handle.read().decode("utf-8", errors="replace")
    lines = [line for line in chunk.splitlines() if line.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def cache_covers_end(path: Path, end_ms: int, *, slack_ms: int = 60_000) -> bool:
    last = read_last_jsonl_record(path)
    if last is None:
        return False
    return int(last["T"]) >= end_ms - slack_ms


def trades_to_jsonl(trades: Sequence[AggTrade]) -> str:
    lines = [
        json.dumps(
            {
                "a": trade.agg_id,
                "p": trade.price,
                "q": trade.qty,
                "T": trade.timestamp_ms,
                "m": trade.is_buyer_maker,
            }
        )
        for trade in trades
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def signed_qty_from_raw(qty: float, is_buyer_maker: bool) -> float:
    trade = AggTrade(agg_id=0, price=0.0, qty=qty, timestamp_ms=0, is_buyer_maker=is_buyer_maker)
    return sign_trade_qty(trade)


def bars_from_trades(trades: Sequence[AggTrade], *, bar_sec: int) -> tuple[list[ClosedBar], float]:
    """Build closed bars; half-spread from adjacent prints (tape, not kline)."""
    if bar_sec <= 0:
        raise ValueError("bar_sec must be positive")
    bucket_ms = bar_sec * 1000
    accs: dict[int, _BarAcc] = {}
    spread_samples: list[float] = []
    prev_price: float | None = None
    for idx, trade in enumerate(trades):
        signed = sign_trade_qty(trade)
        bucket = (trade.timestamp_ms // bucket_ms) * bucket_ms
        acc = accs.get(bucket)
        if acc is None:
            accs[bucket] = _BarAcc(
                open=trade.price,
                high=trade.price,
                low=trade.price,
                close=trade.price,
                signed_qty=signed,
                qty_total=trade.qty,
            )
        else:
            acc.add(trade.price, trade.qty, signed)
        if prev_price is not None and idx % 50 == 0:
            mid = (trade.price + prev_price) / 2.0
            if mid > 0:
                spread_samples.append(abs(trade.price - prev_price) / mid * 10_000.0 / 2.0)
        prev_price = trade.price
    running = 0.0
    bars: list[ClosedBar] = []
    for ts in sorted(accs):
        running += accs[ts].signed_qty
        bars.append(accs[ts].to_bar(ts, running))
    half = float(sorted(spread_samples)[len(spread_samples) // 2]) if spread_samples else 0.0
    return bars, half


def load_closed_bars_from_cache(path: Path, *, bar_sec: int) -> tuple[list[ClosedBar], float, int]:
    """Stream aggTrade jsonl into closed bars without a full trade list."""
    if bar_sec <= 0:
        raise ValueError("bar_sec must be positive")
    bucket_ms = bar_sec * 1000
    accs: dict[int, _BarAcc] = {}
    spread_samples: list[float] = []
    prev_price: float | None = None
    trade_count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            trade_count += 1
            price = float(raw["p"])
            qty = float(raw["q"])
            signed = signed_qty_from_raw(qty, bool(raw["m"]))
            bucket = (int(raw["T"]) // bucket_ms) * bucket_ms
            acc = accs.get(bucket)
            if acc is None:
                accs[bucket] = _BarAcc(
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    signed_qty=signed,
                    qty_total=qty,
                )
            else:
                acc.add(price, qty, signed)
            if prev_price is not None and trade_count % 50 == 0:
                mid = (price + prev_price) / 2.0
                if mid > 0:
                    spread_samples.append(abs(price - prev_price) / mid * 10_000.0 / 2.0)
            prev_price = price
    running = 0.0
    bars: list[ClosedBar] = []
    for ts in sorted(accs):
        running += accs[ts].signed_qty
        bars.append(accs[ts].to_bar(ts, running))
    half = float(sorted(spread_samples)[len(spread_samples) // 2]) if spread_samples else 0.0
    return bars, half, trade_count


def signal_side(bars: Sequence[ClosedBar], i: int, cfg: GridConfig) -> int:
    n = cfg.lookback_n
    if i < n or i + cfg.hold_bars >= len(bars):
        return 0
    prior = bars[i - n : i]
    bar = bars[i]
    prior_high = max(item.high for item in prior)
    prior_low = min(item.low for item in prior)
    prior_cvd_hi = max(item.cvd for item in prior)
    prior_cvd_lo = min(item.cvd for item in prior)
    px_hi = bar.high > prior_high
    px_lo = bar.low < prior_low
    cvd_hi = bar.cvd > prior_cvd_hi
    cvd_lo = bar.cvd < prior_cvd_lo
    if cfg.divergence == "price_ext_cvd_not":
        fired_hi = px_hi and not cvd_hi
        fired_lo = px_lo and not cvd_lo
    elif cfg.divergence == "cvd_ext_price_not":
        fired_hi = cvd_hi and not px_hi
        fired_lo = cvd_lo and not px_lo
    else:
        raise ValueError(f"unknown divergence {cfg.divergence}")
    if fired_hi == fired_lo:
        return 0
    fade = cfg.side_rule == "fade"
    if fired_hi:
        return -1 if fade else 1
    return 1 if fade else -1


def simulate(
    bars: Sequence[ClosedBar],
    cfg: GridConfig,
    *,
    start_ms: int,
    end_ms: int,
    taker_fee_bps: float,
    half_spread_bps: float,
    notional_usd: float,
    start_equity_usd: float,
) -> dict:
    cost_rt = 2.0 * (taker_fee_bps + half_spread_bps)
    pnls: list[float] = []
    for i, bar in enumerate(bars):
        if bar.timestamp_ms < start_ms or bar.timestamp_ms >= end_ms:
            continue
        side = signal_side(bars, i, cfg)
        if side == 0:
            continue
        fill = bars[i + 1]
        if fill.open <= 0:
            continue
        gross_bps = side * (fill.close / fill.open - 1.0) * 10_000.0
        net_bps = gross_bps - cost_rt
        pnls.append(notional_usd * net_bps / 10_000.0)

    n = len(pnls)
    net = float(sum(pnls))
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    if losses < 0:
        profit_factor = wins / abs(losses)
    elif wins > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0
    expectancy = net / n if n else 0.0
    equity = start_equity_usd
    peak = start_equity_usd
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return {
        "id": cfg.id,
        "lookback_n": cfg.lookback_n,
        "divergence": cfg.divergence,
        "side_rule": cfg.side_rule,
        "n": n,
        "net_pnl": net,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "max_dd_pct": max_dd,
        "cost_rt_bps": cost_rt,
        "half_spread_bps": half_spread_bps,
    }


def _pf_rank_key(pf: float) -> float:
    return 1e9 if math.isinf(pf) else pf


def soft_pass(row: dict, gate: dict) -> bool:
    pf = row["profit_factor"]
    pf_ok = math.isinf(pf) or pf >= float(gate["profit_factor_min"])
    return (
        row["n"] >= int(gate["n_trades_min"])
        and row["net_pnl"] > float(gate["net_pnl_gt"])
        and pf_ok
        and row["max_dd_pct"] <= float(gate["max_dd_pct_max"])
    )


def eligible(row: dict, gate: dict) -> bool:
    return row["n"] >= int(gate["n_trades_min"]) and row["net_pnl"] > float(gate["net_pnl_gt"])


def rank_rows(rows: Sequence[dict], gate: dict) -> dict:
    scored = list(rows)
    eligible_rows = [row for row in scored if eligible(row, gate)]
    soft_rows = [row for row in scored if soft_pass(row, gate)]
    ordered = sorted(
        eligible_rows,
        key=lambda row: (_pf_rank_key(row["profit_factor"]), row["expectancy"], row["net_pnl"]),
        reverse=True,
    )
    winner = None
    if soft_rows:
        winner = max(
            soft_rows,
            key=lambda row: (_pf_rank_key(row["profit_factor"]), row["expectancy"], row["net_pnl"]),
        )
    return {
        "eligible_ids": [row["id"] for row in ordered],
        "soft_pass_ids": [row["id"] for row in soft_rows],
        "winner_id": None if winner is None else winner["id"],
        "rows": scored,
    }


def slim_row(row: dict) -> dict:
    pf = row["profit_factor"]
    return {
        "id": row["id"],
        "lookback_n": row["lookback_n"],
        "divergence": row["divergence"],
        "side_rule": row["side_rule"],
        "n": row["n"],
        "net_pnl": round(row["net_pnl"], 4),
        "profit_factor": None if math.isinf(pf) else round(pf, 4),
        "expectancy": round(row["expectancy"], 4),
        "max_dd_pct": round(row["max_dd_pct"], 4),
        "soft_pass": row.get("soft_pass", False),
        "eligible": row.get("eligible", False),
    }


def render_table(title: str, ranked: dict, *, fetch_complete: bool, promote: bool) -> str:
    lines = [
        f"# {title}",
        "",
        f"**promote:** `{str(promote).lower()}`",
        "**live_go:** `false`",
        f"**fetch_complete:** `{str(fetch_complete).lower()}`",
        f"**eligible:** {ranked['eligible_ids'] or 'none'}",
        f"**soft_pass:** {ranked['soft_pass_ids'] or 'none'}",
        f"**winner_id:** {ranked['winner_id']}",
        "",
        "| id | N | div | side | n | NP | PF | E | DD | soft |",
        "|---:|--:|-----|------|--:|---:|---:|--:|---:|:----:|",
    ]
    for row in ranked["rows"]:
        pf = row["profit_factor"]
        pf_s = "inf" if math.isinf(pf) else f"{pf:.3f}"
        lines.append(
            f"| {row['id']} | {row['lookback_n']} | {row['divergence']} | {row['side_rule']} | "
            f"{row['n']} | {row['net_pnl']:.2f} | {pf_s} | {row['expectancy']:.2f} | "
            f"{row['max_dd_pct']:.1%} | {'Y' if row.get('soft_pass') else 'n'} |"
        )
    lines.append("")
    return "\n".join(lines)


async def _request_json(session: aiohttp.ClientSession, params: dict[str, object]) -> object:
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(AGG_TRADES_URL, params=params) as response:
                if response.status == 429:
                    retry_after = float(
                        response.headers.get("Retry-After", RETRY_BASE_SEC * 2**attempt)
                    )
                    LOG.warning("rate limited; sleeping %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                if response.status >= 500:
                    await asyncio.sleep(RETRY_BASE_SEC * 2**attempt)
                    continue
                response.raise_for_status()
                return await response.json()
        except (TimeoutError, aiohttp.ClientError):
            await asyncio.sleep(RETRY_BASE_SEC * 2**attempt)
    raise RuntimeError(f"failed to fetch {AGG_TRADES_URL} after {MAX_RETRIES} retries")


def _append_trades(path: Path, trades: Sequence[AggTrade]) -> None:
    if not trades:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(trades_to_jsonl(trades))


async def fetch_agg_trades_to_cache(
    session: aiohttp.ClientSession,
    symbol: str,
    start: datetime,
    end_exclusive: datetime,
    cache_path: Path,
) -> bool:
    """Paginate aggTrades into jsonl. Resume-safe. Does not load the tape into RAM."""
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end_exclusive.timestamp() * 1000) - 1
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    from_id: int | None = None
    last = read_last_jsonl_record(cache_path)
    if last is not None:
        from_id = int(last["a"]) + 1
        LOG.info("resume %s fromId=%s last_T=%s", cache_path.name, from_id, last.get("T"))
    buffer: list[AggTrade] = []
    fetched = 0

    def flush() -> None:
        nonlocal buffer
        _append_trades(cache_path, buffer)
        buffer = []

    while True:
        params: dict[str, object] = {"symbol": symbol, "limit": AGG_LIMIT}
        if from_id is not None:
            params["fromId"] = from_id
        else:
            params["startTime"] = start_ms
            params["endTime"] = end_ms
        payload = await _request_json(session, params)
        if not isinstance(payload, list) or not payload:
            break
        batch = [parse_agg_trade(item) for item in payload if isinstance(item, dict)]
        if not batch:
            break
        hit_end = False
        for trade in batch:
            if trade.timestamp_ms < start_ms:
                continue
            if trade.timestamp_ms > end_ms:
                hit_end = True
                break
            buffer.append(trade)
            fetched += 1
        if hit_end:
            flush()
            return True
        last_trade = batch[-1]
        next_id = last_trade.agg_id + 1
        if from_id is not None and next_id <= from_id:
            break
        from_id = next_id
        if len(buffer) >= CACHE_FLUSH_EVERY:
            flush()
        if fetched > 0 and fetched % 50_000 < AGG_LIMIT:
            LOG.info("%s: %d aggTrades appended", symbol, fetched)
        await asyncio.sleep(REQUEST_DELAY_SEC)
    flush()
    return cache_covers_end(cache_path, end_ms)


def screen_window(
    bars: Sequence[ClosedBar],
    lock: dict,
    *,
    start_ms: int,
    end_ms: int,
    half_spread_bps: float,
) -> dict:
    gate = lock["soft_gate"]
    fee = float(lock["costs"]["taker_fee_bps"])
    book = lock["book"]
    rows = []
    for cfg in grid_from_lock(lock):
        row = simulate(
            bars,
            cfg,
            start_ms=start_ms,
            end_ms=end_ms,
            taker_fee_bps=fee,
            half_spread_bps=half_spread_bps,
            notional_usd=float(book["notional_usd"]),
            start_equity_usd=float(book["start_equity_usd"]),
        )
        row["soft_pass"] = soft_pass(row, gate)
        row["eligible"] = eligible(row, gate)
        rows.append(row)
    ranked = rank_rows(rows, gate)
    ranked["rows"] = rows
    return ranked


def write_rank_artifacts(
    out_dir: Path,
    *,
    stem: str,
    ranked: dict,
    lock: dict,
    extra: dict,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "search_id": lock["search_id"],
        "promote": False,
        "live_go": False,
        "winner_id": ranked["winner_id"],
        "eligible_ids": ranked["eligible_ids"],
        "soft_pass_ids": ranked["soft_pass_ids"],
        "rows": [slim_row(row) for row in ranked["rows"]],
        **extra,
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(
        render_table(
            f"CVD absorption v1 — {stem}",
            ranked,
            fetch_complete=bool(extra.get("fetch_complete")),
            promote=False,
        ),
        encoding="utf-8",
    )


async def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=60, connect=10),
        headers={"Accept": "application/json"},
    )


async def run_fetch(lock: dict, cache_dir: Path, *, holdout: bool) -> bool:
    symbol = lock["symbol"]
    if holdout:
        start = _parse_dt(lock["holdout_start"])
        end = _parse_dt(lock["holdout_end_exclusive"])
    else:
        start = _parse_dt(lock["develop_start"])
        end = _parse_dt(lock["develop_end_exclusive"])
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    assert_window_legal(start_ms, end_ms, lock)
    cache_path = cache_path_for(cache_dir, symbol, start, end)
    assert_legal_cache_path(cache_path, lock)
    if cache_covers_end(cache_path, end_ms - 1):
        LOG.info("cache already complete: %s", cache_path)
        return True
    async with await _session() as session:
        complete = await fetch_agg_trades_to_cache(session, symbol, start, end, cache_path)
    LOG.info("fetch complete=%s path=%s", complete, cache_path)
    return complete


def run_rank(lock: dict, cache_dir: Path, out_dir: Path, *, holdout: bool) -> dict:
    symbol = lock["symbol"]
    if holdout:
        start = _parse_dt(lock["holdout_start"])
        end = _parse_dt(lock["holdout_end_exclusive"])
        stem = "holdout_winner"
        develop_payload = json.loads((out_dir / "develop_rank.json").read_text(encoding="utf-8"))
        winner_id = develop_payload.get("winner_id")
        if winner_id is None:
            raise LockTamper("holdout refused: no develop winner")
    else:
        start = _parse_dt(lock["develop_start"])
        end = _parse_dt(lock["develop_end_exclusive"])
        stem = "develop_rank"
        winner_id = None
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    assert_window_legal(start_ms, end_ms, lock)
    cache_path = cache_path_for(cache_dir, symbol, start, end)
    assert_legal_cache_path(cache_path, lock)
    complete = cache_covers_end(cache_path, end_ms - 1)
    if not complete:
        extra = {
            "fetch_complete": False,
            "window": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
            "blocked_reason": "develop fetch incomplete"
            if not holdout
            else "holdout fetch incomplete",
            "cache_path": str(cache_path),
        }
        empty = {
            "eligible_ids": [],
            "soft_pass_ids": [],
            "winner_id": None,
            "rows": [],
        }
        write_rank_artifacts(out_dir, stem=stem, ranked=empty, lock=lock, extra=extra)
        return {**empty, **extra, "stem": stem}

    bars, half_spread, trade_count = load_closed_bars_from_cache(
        cache_path, bar_sec=int(lock["bar_sec"])
    )
    ranked = screen_window(
        bars, lock, start_ms=start_ms, end_ms=end_ms, half_spread_bps=half_spread
    )
    if holdout:
        ranked["rows"] = [row for row in ranked["rows"] if row["id"] == winner_id]
        ranked["winner_id"] = winner_id
        ranked["eligible_ids"] = [row["id"] for row in ranked["rows"] if row["eligible"]]
        ranked["soft_pass_ids"] = [row["id"] for row in ranked["rows"] if row["soft_pass"]]
    extra = {
        "fetch_complete": True,
        "window": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "cache_path": str(cache_path),
        "trade_count": trade_count,
        "bar_count": len(bars),
        "half_spread_bps": half_spread,
        "taker_fee_bps": 10.0,
    }
    write_rank_artifacts(out_dir, stem=stem, ranked=ranked, lock=lock, extra=extra)
    return {**ranked, **extra, "stem": stem}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--phase",
        choices=("fetch-develop", "rank-develop", "fetch-holdout", "score-holdout"),
        default="fetch-develop",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    lock = load_and_validate_lock(Path(args.lock))
    cache_dir = Path(args.cache_dir or lock["cache_dir"])
    if not cache_dir.is_absolute():
        cache_dir = REPO_ROOT / cache_dir
    assert_legal_cache_path(cache_dir / "BTCUSDT" / "probe.jsonl", lock)
    out_dir = Path(args.output_dir)

    if args.phase == "fetch-develop":
        complete = asyncio.run(run_fetch(lock, cache_dir, holdout=False))
        print(f"develop_fetch_complete={complete}")
        return 0 if complete else 2
    if args.phase == "rank-develop":
        result = run_rank(lock, cache_dir, out_dir, holdout=False)
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in ("winner_id", "eligible_ids", "soft_pass_ids", "fetch_complete")
                    if k in result
                },
                indent=2,
            )
        )
        return 0 if result.get("fetch_complete") else 2
    if args.phase == "fetch-holdout":
        develop = out_dir / "develop_rank.json"
        if not develop.is_file():
            raise LockTamper("holdout fetch refused: develop_rank.json missing")
        payload = json.loads(develop.read_text(encoding="utf-8"))
        if payload.get("winner_id") is None:
            raise LockTamper("holdout fetch refused: no develop winner")
        complete = asyncio.run(run_fetch(lock, cache_dir, holdout=True))
        print(f"holdout_fetch_complete={complete}")
        return 0 if complete else 2
    if args.phase == "score-holdout":
        result = run_rank(lock, cache_dir, out_dir, holdout=True)
        print(
            json.dumps(
                {k: result[k] for k in ("winner_id", "fetch_complete") if k in result}, indent=2
            )
        )
        return 0 if result.get("fetch_complete") else 2
    raise AssertionError(args.phase)


if __name__ == "__main__":
    # Research script only. Do not start the agent from here.
    if any(token in sys.argv for token in ("--live", "--promote")):
        raise SystemExit("live/promote flags are refused")
    raise SystemExit(main())
