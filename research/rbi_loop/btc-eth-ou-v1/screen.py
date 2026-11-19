"""Locked develop screen for `binance_spot_btc_eth_logspread_ou_v1` (CR-3).

Executes docs/evidence_portfolio/BTC_ETH_OU_PREREG.md exactly as locked in
research/rbi_loop/btc-eth-ou-v1/lock.json. Every parameter is read from the lock;
nothing here is tunable.

Usage:
    uv run python research/rbi_loop/btc-eth-ou-v1/screen.py            # use cached klines
    uv run python research/rbi_loop/btc-eth-ou-v1/screen.py --fetch    # (re)fetch, then screen

Fetch scope: Binance spot public REST, daily klines from (develop_start - min_obs days)
to the last bar that opens before develop_end_exclusive. Nothing at or after
develop_end_exclusive is requested or accepted, so the holdout stays sealed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

LANE_DIR = Path(__file__).resolve().parent
REPO_ROOT = LANE_DIR.parents[2]
KLINE_DIR = LANE_DIR / "klines"
MANIFEST_PATH = KLINE_DIR / "fetch_manifest.json"
RESULT_PATH = LANE_DIR / "screen_result.json"

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
DAY_MS = 86_400_000
CSV_FIELDS = [
    "date_utc",
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_ms",
    "quote_volume",
    "n_trades",
]

LOCK = json.loads((LANE_DIR / "lock.json").read_text())
SYMBOLS: list[str] = LOCK["symbols"]
MIN_OBS = int(LOCK["beta"].split("min_obs")[1].strip())
Z_ENTRY = float(LOCK["z_entry"])
Z_EXIT = float(LOCK["z_exit"])
MAX_HOLD = int(LOCK["max_hold_days"])
FEE = float(LOCK["costs"]["taker_fee_bps"]) / 10_000.0
START_EQUITY = float(LOCK["book"]["start_equity_usd"])
BTC_NOTIONAL = float(LOCK["book"]["btc_notional_usd"])
GATE = LOCK["soft_gate"]


def _parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


DEVELOP_START = _parse_utc(LOCK["develop_start"])
DEVELOP_END = _parse_utc(LOCK["develop_end_exclusive"])
HOLDOUT_START = _parse_utc(LOCK["holdout_start"])
FIT_START = DEVELOP_START - timedelta(days=MIN_OBS)


def _assert_lock_matches_prereg() -> None:
    """Fail loudly if the lock file drifted from the values the prereg states."""
    expected = {
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "tf": "1d",
        "venue": "binance_spot",
        "z_entry": 2.0,
        "z_exit": 0.0,
        "max_hold_days": 5,
        "n_free_knobs": 0,
    }
    for key, value in expected.items():
        if LOCK[key] != value:
            raise RuntimeError(f"lock.json {key}={LOCK[key]!r} != prereg {value!r}")
    if MIN_OBS != 252 or FEE != 0.001 or BTC_NOTIONAL != 1000.0 or START_EQUITY != 10_000.0:
        raise RuntimeError("lock.json numeric parameters drifted from the prereg")
    if LOCK["book"]["compound_size"] or LOCK["book"]["leverage"] != 1:
        raise RuntimeError("lock.json book drifted from the prereg")
    if HOLDOUT_START != DEVELOP_END:
        raise RuntimeError("holdout must start where develop ends")


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _get_json(url: str) -> list[list]:
    """GET with bounded retries; honours Binance 429/418 Retry-After."""
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (418, 429):
                wait = int(exc.headers.get("Retry-After", "60"))
            elif exc.code >= 500:
                wait = 2**attempt
            else:
                raise
        except urllib.error.URLError:
            wait = 2**attempt
        time.sleep(wait)
    raise RuntimeError(f"Binance unreachable after retries: {url}")


def fetch_symbol(symbol: str) -> dict:
    """Fetch closed daily klines in [FIT_START, DEVELOP_END) and write the CSV cache."""
    start_ms = _ms(FIT_START)
    end_ms = _ms(DEVELOP_END) - 1
    rows: list[list] = []
    requests: list[dict] = []
    cursor = start_ms
    while cursor <= end_ms:
        query = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": LOCK["tf"],
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        url = f"{BINANCE_KLINES_URL}?{query}"
        batch = _get_json(url)
        requests.append(
            {"url": url, "fetched_at_utc": datetime.now(UTC).isoformat(), "rows": len(batch)}
        )
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1][0]) + DAY_MS
        if len(batch) < 1000:
            break
        time.sleep(0.25)

    for k in rows:
        if not start_ms <= int(k[0]) < _ms(DEVELOP_END):
            raise RuntimeError(f"{symbol}: kline open {k[0]} outside fetch scope")
    fetched_at = datetime.now(UTC)
    if rows and int(rows[-1][6]) >= _ms(fetched_at):
        raise RuntimeError(f"{symbol}: last kline not closed at fetch time")

    KLINE_DIR.mkdir(parents=True, exist_ok=True)
    path = KLINE_DIR / f"{symbol}_1d.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_FIELDS)
        for k in rows:
            day = datetime.fromtimestamp(int(k[0]) / 1000, UTC).date().isoformat()
            writer.writerow([day, k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7], k[8]])
    return {
        "symbol": symbol,
        "path": str(path.relative_to(REPO_ROOT)),
        "requests": requests,
        "rows": len(rows),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def fetch_all() -> None:
    manifest = {
        "source": BINANCE_KLINES_URL,
        "api_key": False,
        "scope_start_utc": FIT_START.isoformat(),
        "scope_end_exclusive_utc": DEVELOP_END.isoformat(),
        "holdout_fetched": False,
        "symbols": [fetch_symbol(s) for s in SYMBOLS],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    mirror = REPO_ROOT / LOCK["cache_dir"]
    mirror.mkdir(parents=True, exist_ok=True)
    for item in manifest["symbols"]:
        src = REPO_ROOT / item["path"]
        (mirror / src.name).write_bytes(src.read_bytes())


def load_symbol(symbol: str) -> dict[str, np.ndarray]:
    path = KLINE_DIR / f"{symbol}_1d.csv"
    with path.open() as fh:
        records = list(csv.DictReader(fh))
    return {
        "date": np.array([r["date_utc"] for r in records]),
        "open_time_ms": np.array([int(r["open_time_ms"]) for r in records], dtype=np.int64),
        **{f: np.array([float(r[f]) for r in records]) for f in ("open", "high", "low", "close")},
    }


def integrity(data: dict[str, dict[str, np.ndarray]]) -> dict:
    expected_days = (DEVELOP_END - FIT_START).days
    expected_ms = _ms(FIT_START) + DAY_MS * np.arange(expected_days, dtype=np.int64)
    report: dict = {"expected_days": expected_days}
    for symbol, d in data.items():
        t = d["open_time_ms"]
        ohlc_ok = bool(
            np.all(d["low"] > 0)
            & np.all(d["low"] <= np.minimum(d["open"], d["close"]))
            & np.all(d["high"] >= np.maximum(d["open"], d["close"]))
        )
        report[symbol] = {
            "rows": int(len(t)),
            "first": str(d["date"][0]),
            "last": str(d["date"][-1]),
            "duplicates": int(len(t) - len(np.unique(t))),
            "missing_days": [
                datetime.fromtimestamp(int(m) / 1000, UTC).date().isoformat()
                for m in np.setdiff1d(expected_ms, t)
            ],
            "unexpected_days": int(len(np.setdiff1d(t, expected_ms))),
            "strictly_increasing": bool(np.all(np.diff(t) > 0)),
            "ohlc_sane": ohlc_ok,
            "rows_at_or_after_holdout_start": int(np.sum(t >= _ms(HOLDOUT_START))),
        }
    a, b = (data[s]["open_time_ms"] for s in SYMBOLS)
    report["dates_aligned"] = bool(len(a) == len(b) and np.array_equal(a, b))
    clean = report["dates_aligned"] and all(
        report[s]["rows"] == expected_days
        and report[s]["duplicates"] == 0
        and not report[s]["missing_days"]
        and report[s]["unexpected_days"] == 0
        and report[s]["strictly_increasing"]
        and report[s]["ohlc_sane"]
        and report[s]["rows_at_or_after_holdout_start"] == 0
        for s in SYMBOLS
    )
    report["clean"] = bool(clean)
    return report


def expanding_fit(log_btc: np.ndarray, log_eth: np.ndarray, first_signal: int) -> dict:
    """z[t] uses alpha/beta/sigma fitted on rows [0, t) only (strictly before close t)."""
    n = len(log_btc)
    alpha, beta, sigma, z = (np.full(n, np.nan) for _ in range(4))
    for t in range(first_signal, n):
        if t < MIN_OBS:
            raise RuntimeError(f"row {t}: only {t} fit observations < {MIN_OBS}")
        x, y = log_eth[:t], log_btc[:t]
        design = np.column_stack([np.ones(t), x])
        coef, *_ = np.linalg.lstsq(design, y, rcond=None)
        resid = y - design @ coef
        alpha[t], beta[t] = coef
        sigma[t] = np.sqrt(resid @ resid / (t - 2))
        z[t] = (log_btc[t] - alpha[t] - beta[t] * log_eth[t]) / sigma[t]
    return {"alpha": alpha, "beta": beta, "sigma": sigma, "z": z}


def simulate(
    dates: np.ndarray,
    btc: dict[str, np.ndarray],
    eth: dict[str, np.ndarray],
    fit: dict[str, np.ndarray],
    first_dev: int,
    last_dev: int,
) -> tuple[list[dict], list[dict], list[tuple[str, float]]]:
    """One position at a time. Signal at close i, enter open i+1, exit z-cross or open i+5."""
    z, beta = fit["z"], fit["beta"]
    trades: list[dict] = []
    skipped: list[dict] = []
    marks: list[tuple[str, float]] = [(f"{dates[first_dev]} start", START_EQUITY)]
    realized = 0.0
    i = first_dev
    while i <= last_dev:
        if not abs(z[i]) > Z_ENTRY:
            i += 1
            continue
        cap = i + MAX_HOLD
        if cap > last_dev:
            skipped.append(
                {"signal_date": str(dates[i]), "z": float(z[i]), "reason": "exit_after_develop"}
            )
            i += 1
            continue
        side = -1 if z[i] > 0 else 1  # -1 = short spread (short BTC, long ETH)
        entry, exit_, reason = i + 1, cap, "max_hold"
        for j in range(i + 1, cap):
            if side * (z[j] - Z_EXIT) >= 0:
                exit_, reason = j + 1, "z_cross_0"
                break
        b = float(beta[i])
        pb0, pe0 = btc["open"][entry], eth["open"][entry]
        pb1, pe1 = btc["open"][exit_], eth["open"][exit_]
        q_btc = side * BTC_NOTIONAL / pb0
        q_eth = -side * b * BTC_NOTIONAL / pe0
        entry_fee = FEE * (abs(q_btc) * pb0 + abs(q_eth) * pe0)
        exit_fee = FEE * (abs(q_btc) * pb1 + abs(q_eth) * pe1)
        btc_pnl = q_btc * (pb1 - pb0)
        eth_pnl = q_eth * (pe1 - pe0)
        net = btc_pnl + eth_pnl - entry_fee - exit_fee

        marks.append((f"{dates[entry]} open (entry)", START_EQUITY + realized - entry_fee))
        for k in range(entry, exit_):
            mtm = q_btc * (btc["close"][k] - pb0) + q_eth * (eth["close"][k] - pe0)
            marks.append((f"{dates[k]} close", START_EQUITY + realized - entry_fee + mtm))
        realized += net
        marks.append((f"{dates[exit_]} open (exit)", START_EQUITY + realized))

        trades.append(
            {
                "n": len(trades) + 1,
                "signal_date": str(dates[i]),
                "z_signal": float(z[i]),
                "side": "short_spread" if side < 0 else "long_spread",
                "beta": b,
                "entry_date": str(dates[entry]),
                "exit_date": str(dates[exit_]),
                "exit_reason": reason,
                "hold_days": int(exit_ - entry),
                "btc_entry": float(pb0),
                "btc_exit": float(pb1),
                "eth_entry": float(pe0),
                "eth_exit": float(pe1),
                "eth_notional": float(b * BTC_NOTIONAL),
                "btc_leg_pnl": float(btc_pnl),
                "eth_leg_pnl": float(eth_pnl),
                "fees": float(entry_fee + exit_fee),
                "net_pnl": float(net),
                "cum_net_pnl": float(realized),
            }
        )
        i = exit_
    return trades, skipped, marks


def max_drawdown(values: list[float]) -> tuple[float, float]:
    """Return (max dd as fraction of running peak, max dd in USD)."""
    arr = np.asarray(values)
    peak = np.maximum.accumulate(arr)
    dd = peak - arr
    k = int(np.argmax(dd / peak))
    return float(dd[k] / peak[k]), float(dd.max())


def metrics(trades: list[dict], marks: list[tuple[str, float]]) -> dict:
    net = np.array([t["net_pnl"] for t in trades])
    wins, losses = net[net > 0], net[net <= 0]
    gross_loss = float(-losses.sum())
    pf = float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf")
    dd_mtm, dd_mtm_usd = max_drawdown([v for _, v in marks])
    closed = [START_EQUITY, *(START_EQUITY + t["cum_net_pnl"] for t in trades)]
    dd_closed, dd_closed_usd = max_drawdown(closed)
    reasons: dict[str, int] = {}
    sides: dict[str, dict] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
        s = sides.setdefault(t["side"], {"n": 0, "net_pnl": 0.0})
        s["n"] += 1
        s["net_pnl"] += t["net_pnl"]
    return {
        "n_trades": int(len(net)),
        "net_pnl_usd": float(net.sum()),
        "net_return_on_start_equity": float(net.sum() / START_EQUITY),
        "profit_factor": pf,
        "gross_profit_usd": float(wins.sum()),
        "gross_loss_usd": gross_loss,
        "total_fees_usd": float(sum(t["fees"] for t in trades)),
        "pnl_before_fees_usd": float(sum(t["btc_leg_pnl"] + t["eth_leg_pnl"] for t in trades)),
        "win_rate": float(len(wins) / len(net)),
        "n_wins": int(len(wins)),
        "n_losses": int(len(losses)),
        "mean_net_pnl_usd": float(net.mean()),
        "median_net_pnl_usd": float(np.median(net)),
        "std_net_pnl_usd": float(net.std(ddof=1)),
        "mean_win_usd": float(wins.mean()) if len(wins) else 0.0,
        "mean_loss_usd": float(losses.mean()) if len(losses) else 0.0,
        "best_trade_usd": float(net.max()),
        "worst_trade_usd": float(net.min()),
        "mean_hold_days": float(np.mean([t["hold_days"] for t in trades])),
        "exit_reasons": reasons,
        "by_side": sides,
        "max_dd_pct_mtm": dd_mtm,
        "max_dd_usd_mtm": dd_mtm_usd,
        "max_dd_pct_closed_trade": dd_closed,
        "max_dd_usd_closed_trade": dd_closed_usd,
        "final_equity_usd": float(START_EQUITY + net.sum()),
    }


def evaluate_gates(m: dict) -> dict:
    gates = {
        "n_trades_min": {
            "threshold": f">= {GATE['n_trades_min']}",
            "value": m["n_trades"],
            "pass": m["n_trades"] >= GATE["n_trades_min"],
        },
        "profit_factor_min": {
            "threshold": f">= {GATE['profit_factor_min']}",
            "value": m["profit_factor"],
            "pass": m["profit_factor"] >= GATE["profit_factor_min"],
        },
        "net_pnl_gt": {
            "threshold": f"> {GATE['net_pnl_gt']}",
            "value": m["net_pnl_usd"],
            "pass": m["net_pnl_usd"] > GATE["net_pnl_gt"],
        },
        "max_dd_pct_max": {
            "threshold": f"<= {GATE['max_dd_pct_max']} (mark-to-market)",
            "value": m["max_dd_pct_mtm"],
            "pass": m["max_dd_pct_mtm"] <= GATE["max_dd_pct_max"],
        },
    }
    return {k: {**v, "pass": bool(v["pass"])} for k, v in gates.items()}


def fit_summary(dates: np.ndarray, fit: dict, first_dev: int, last_dev: int) -> dict:
    sl = slice(first_dev, last_dev + 1)
    z = fit["z"][sl]

    def rng(a: np.ndarray) -> dict:
        return {
            "first": float(a[0]),
            "last": float(a[-1]),
            "min": float(a.min()),
            "max": float(a.max()),
        }

    return {
        "fit_window_start": str(dates[0]),
        "fit_obs_first_signal": int(first_dev),
        "fit_obs_last_signal": int(last_dev),
        "alpha": rng(fit["alpha"][sl]),
        "beta": rng(fit["beta"][sl]),
        "sigma_resid": rng(fit["sigma"][sl]),
        "z": {
            **rng(z),
            "mean": float(z.mean()),
            "std": float(z.std(ddof=1)),
            "lag1_autocorr": float(np.corrcoef(z[:-1], z[1:])[0, 1]),
            "days_gt_entry": int(np.sum(z > Z_ENTRY)),
            "days_lt_neg_entry": int(np.sum(z < -Z_ENTRY)),
            "develop_days": int(len(z)),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fetch", action="store_true", help="(re)fetch klines from Binance")
    args = parser.parse_args()

    _assert_lock_matches_prereg()
    if args.fetch or not all((KLINE_DIR / f"{s}_1d.csv").exists() for s in SYMBOLS):
        fetch_all()

    data = {s: load_symbol(s) for s in SYMBOLS}
    checks = integrity(data)
    if not checks["clean"]:
        raise RuntimeError(f"data integrity failed: {json.dumps(checks, indent=2)}")

    btc, eth = data["BTCUSDT"], data["ETHUSDT"]
    dates = btc["date"]
    first_dev = int(np.searchsorted(btc["open_time_ms"], _ms(DEVELOP_START)))
    last_dev = len(dates) - 1
    if dates[first_dev] != DEVELOP_START.date().isoformat() or first_dev != MIN_OBS:
        raise RuntimeError("develop start misaligned with fit warmup")

    fit = expanding_fit(np.log(btc["close"]), np.log(eth["close"]), first_dev)
    trades, skipped, marks = simulate(dates, btc, eth, fit, first_dev, last_dev)
    m = metrics(trades, marks)
    gates = evaluate_gates(m)
    verdict = "SCREEN_PASS" if all(g["pass"] for g in gates.values()) else "SCREEN_FAIL"

    result = {
        "search_id": LOCK["search_id"],
        "run_at_utc": datetime.now(UTC).isoformat(),
        "develop": [LOCK["develop_start"], LOCK["develop_end_exclusive"]],
        "holdout_touched": False,
        "integrity": checks,
        "fit": fit_summary(dates, fit, first_dev, last_dev),
        "skipped_signals": skipped,
        "metrics": m,
        "gates": gates,
        "verdict": verdict,
        "trades": trades,
    }
    RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"{verdict}: n={m['n_trades']} PF={m['profit_factor']:.3f} "
        f"NP=${m['net_pnl_usd']:.2f} DD={m['max_dd_pct_mtm']:.2%} -> {RESULT_PATH.name}"
    )


if __name__ == "__main__":
    main()
