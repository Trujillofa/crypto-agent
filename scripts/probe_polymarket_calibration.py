#!/usr/bin/env python3
"""Cheap feasibility probe for Polymarket calibration / favorite-longshot bias (Gate 1).

Thesis: resolved binary markets on Polymarket may exhibit systematic probability
mispricing (favorite-longshot bias) — longshots overpriced, favorites underpriced —
detectable by comparing pre-resolution YES prices at lead time τ to realized outcomes.

STEP 0: pull resolved binary markets from the public Gamma API; require price at τ
from the CLOB prices-history endpoint. If usable_markets < MIN_MARKETS → BLOCKED_ON_DATA.

STEP 1: bucket by decile of YES price p at τ; test calibration edge vs round-trip cost.
  H1: ≥1 bucket shows |edge| beyond round-trip cost with binomial significance (Bonferroni).
  H2: qualifying mispricing survives a time split (older vs newer half) AND survives
      excluding the largest category.
  HAS_PULSE := H1 ∧ H2.  WEAK_EDGE := H1 only (incl. single-category).  NO_PULSE := neither.

Read-only. No wallet, no on-chain tx, no orders, no --execute.
See docs/specs/polymarket-calibration-probe-v0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.logger import configure_logger, get_logger

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
PRICES_HISTORY_URL = f"{CLOB_BASE}/prices-history"

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
HAS_PULSE = "HAS_PULSE"
WEAK_EDGE = "WEAK_EDGE"
NO_PULSE = "NO_PULSE"

DEFAULT_START = "2024-12-20T00:00:00Z"
DEFAULT_END = "2026-06-20T00:00:00Z"
DEFAULT_LEAD_HOURS = (24, 72)
DEFAULT_BUCKETS = 10
MIN_MARKETS = 300
DEFAULT_MIN_LIQUIDITY = 1000.0
DEFAULT_ROUND_TRIP_COST_PCT = 2.5
MIN_BUCKET_SAMPLES = 10
ALPHA = 0.05
# Resolved/closed markets only return sub-12h granularity from prices-history (fidelity ≥ 720 min).
CLOB_MIN_FIDELITY_MINUTES = 720
PRICE_FETCH_CONCURRENCY = 8

DISPUTED_PATTERNS = re.compile(
    r"\b(iran\b|disputed|oracle.?contest|resolution.?contest|integrity.?committee|"
    r"vibe.?invest|manipulat|mic.?overturn)",
    re.IGNORECASE,
)

POLITICS_CATEGORIES = frozenset(
    {
        "us-current-affairs",
        "politics",
        "global-politics",
        "elections",
        "world",
        "geopolitics",
    }
)
SPORTS_CATEGORIES = frozenset({"sports", "nba", "nfl", "mlb", "nhl", "soccer", "mma", "esports"})
CRYPTO_CATEGORIES = frozenset({"crypto", "bitcoin", "ethereum", "defi", "nft"})


@dataclass(frozen=True)
class ProbeConfig:
    start: str
    end: str
    lead_hours: tuple[int, ...]
    buckets: int
    min_markets: int
    round_trip_cost_pct: float
    min_liquidity: float
    cache_file: Path | None
    max_price_fetch: int | None


@dataclass(frozen=True)
class ResolvedMarket:
    market_id: str
    question: str
    condition_id: str
    yes_token_id: str
    category: str
    category_group: str
    volume_usd: float
    closed_time: datetime
    outcome_yes: int
    disputed_flag: bool
    slug: str


@dataclass(frozen=True)
class MarketObservation:
    market: ResolvedMarket
    lead_hours: int
    price_at_tau: float


@dataclass(frozen=True)
class CalibrationBucket:
    bucket_index: int
    price_low: float
    price_high: float
    n: int
    mean_price: float
    realized_freq: float
    edge: float
    net_edge: float
    binom_p_raw: float
    binom_p_adj: float
    significant: bool
    tradeable: bool


@dataclass(frozen=True)
class LeadTimeResult:
    lead_hours: int
    observations: int
    buckets: tuple[CalibrationBucket, ...]
    qualifying_bucket_indices: tuple[int, ...]
    h1_pass: bool
    h2_time_split_pass: bool
    h2_category_exclusion_pass: bool
    h2_pass: bool
    single_category_only: bool
    dominant_category: str | None


@dataclass(frozen=True)
class DataAudit:
    total_pulled: int
    exclusions: dict[str, int]
    disputed_flagged: int
    with_price_by_lead: dict[str, int]
    usable_for_edge: int
    category_mix: dict[str, int]
    blocked: bool
    blocked_reason: str | None


@dataclass(frozen=True)
class ProbeReport:
    config: dict[str, object]
    data_audit: DataAudit
    lead_results: tuple[LeadTimeResult, ...]
    status: str
    verdict: str
    reasons: tuple[str, ...]
    cost_assumption_note: str = field(
        default=(
            "round_trip_cost = half-spread (~1.0% conservative) + Polymarket taker fee "
            "(0% on most markets per CLOB metadata) + Polygon gas/settlement allowance "
            "(~0.5%) + slippage buffer; default 2.5% total, override via --round-trip-cost-pct"
        )
    )


def _parse_dt(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    if "+" not in text[10:] and text[-1] not in "Z":
        text += "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def normalize_category_group(raw_category: str | None, question: str) -> str:
    cat = (raw_category or "").strip().lower()
    if cat in POLITICS_CATEGORIES or any(
        k in question.lower()
        for k in ("election", "president", "congress", "senate", "trump", "biden")
    ):
        return "politics"
    if cat in SPORTS_CATEGORIES or any(
        k in question.lower() for k in (" vs ", " vs. ", " win on ")
    ):
        return "sports"
    if cat in CRYPTO_CATEGORIES or any(
        k in question.lower() for k in ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana")
    ):
        return "crypto"
    return "other"


def is_disputed_market(question: str, description: str = "") -> bool:
    return bool(DISPUTED_PATTERNS.search(f"{question} {description}"))


def classify_raw_market(raw: dict[str, object]) -> tuple[str | None, str]:
    """Return (ResolvedMarket-as-dict-ready, exclusion_reason). exclusion_reason '' if keep."""
    uma = str(raw.get("umaResolutionStatus") or "")
    if uma != "resolved":
        return None, "unresolved"

    try:
        outcomes = json.loads(str(raw.get("outcomes") or "[]"))
        prices = json.loads(str(raw.get("outcomePrices") or "[]"))
        token_ids = json.loads(str(raw.get("clobTokenIds") or "[]"))
    except json.JSONDecodeError:
        return None, "parse_error"

    if len(outcomes) != 2 or len(prices) != 2:
        return None, "not_binary"

    if prices[0] == prices[1] == "0.5":
        return None, "invalid_refunded"

    if not all(p in ("0", "1") for p in prices) or prices[0] == prices[1]:
        return None, "invalid_outcome"

    volume = float(raw.get("volumeNum") or raw.get("volume") or 0.0)
    if volume <= 0:
        return None, "zero_volume"

    closed_raw = raw.get("closedTime") or raw.get("endDate")
    if not closed_raw:
        return None, "no_close_time"

    if not token_ids:
        return None, "no_clob_tokens"

    question = str(raw.get("question") or "")
    description = str(raw.get("description") or "")
    if is_disputed_market(question, description):
        return None, "disputed_resolution"

    outcome_yes = 1 if prices[0] == "1" else 0
    closed_time = _parse_dt(str(closed_raw))
    category = str(raw.get("category") or "unknown")
    market = ResolvedMarket(
        market_id=str(raw.get("id") or ""),
        question=question,
        condition_id=str(raw.get("conditionId") or ""),
        yes_token_id=str(token_ids[0]),
        category=category,
        category_group=normalize_category_group(category, question),
        volume_usd=volume,
        closed_time=closed_time,
        outcome_yes=outcome_yes,
        disputed_flag=False,
        slug=str(raw.get("slug") or ""),
    )
    return market, ""


def filter_for_liquidity(market: ResolvedMarket, min_liquidity: float) -> str | None:
    if market.volume_usd < min_liquidity:
        return "low_liquidity"
    return None


def _binom_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return math.comb(n, k) * (p**k) * ((1.0 - p) ** (n - k))


def two_sided_binom_pvalue(successes: int, n: int, p0: float) -> float:
    """Two-sided binomial test: H0 true probability = p0."""
    if n <= 0:
        return 1.0
    p0 = min(max(p0, 1e-9), 1.0 - 1e-9)
    obs = sum(_binom_pmf(k, n, p0) for k in range(successes + 1))
    # Mirror tail for two-sided
    tail = min(obs, sum(_binom_pmf(k, n, p0) for k in range(successes, n + 1)))
    return min(1.0, 2.0 * tail)


def assign_bucket(price: float, buckets: int) -> int:
    clamped = min(max(price, 0.0), 0.999999)
    return min(int(clamped * buckets), buckets - 1)


def build_observations(
    markets: Sequence[ResolvedMarket],
    prices_by_market_lead: dict[tuple[str, int], float],
    lead_hours: int,
) -> list[MarketObservation]:
    out: list[MarketObservation] = []
    for market in markets:
        key = (market.market_id, lead_hours)
        price = prices_by_market_lead.get(key)
        if price is None:
            continue
        out.append(MarketObservation(market=market, lead_hours=lead_hours, price_at_tau=price))
    return out


def compute_calibration_buckets(
    observations: Sequence[MarketObservation],
    buckets: int,
    round_trip_cost_pct: float,
) -> tuple[CalibrationBucket, ...]:
    cost = round_trip_cost_pct / 100.0
    grouped: dict[int, list[MarketObservation]] = {i: [] for i in range(buckets)}
    for obs in observations:
        grouped[assign_bucket(obs.price_at_tau, buckets)].append(obs)

    results: list[CalibrationBucket] = []
    for idx in range(buckets):
        items = grouped[idx]
        n = len(items)
        if n == 0:
            results.append(
                CalibrationBucket(
                    bucket_index=idx,
                    price_low=idx / buckets,
                    price_high=(idx + 1) / buckets,
                    n=0,
                    mean_price=0.0,
                    realized_freq=0.0,
                    edge=0.0,
                    net_edge=0.0,
                    binom_p_raw=1.0,
                    binom_p_adj=1.0,
                    significant=False,
                    tradeable=False,
                )
            )
            continue

        prices = [o.price_at_tau for o in items]
        outcomes = [o.market.outcome_yes for o in items]
        mean_price = sum(prices) / n
        realized_freq = sum(outcomes) / n
        edge = mean_price - realized_freq
        net_edge = abs(edge) - cost
        successes = sum(outcomes)
        p_raw = two_sided_binom_pvalue(successes, n, mean_price)
        p_adj = min(1.0, p_raw * buckets)
        significant = n >= MIN_BUCKET_SAMPLES and p_adj < ALPHA
        tradeable = significant and net_edge > 0.0
        results.append(
            CalibrationBucket(
                bucket_index=idx,
                price_low=idx / buckets,
                price_high=(idx + 1) / buckets,
                n=n,
                mean_price=mean_price,
                realized_freq=realized_freq,
                edge=edge,
                net_edge=net_edge,
                binom_p_raw=p_raw,
                binom_p_adj=p_adj,
                significant=significant,
                tradeable=tradeable,
            )
        )
    return tuple(results)


def _qualifying_indices(buckets: Sequence[CalibrationBucket]) -> tuple[int, ...]:
    return tuple(b.bucket_index for b in buckets if b.tradeable)


def _bucket_edge_sign_map(
    observations: Sequence[MarketObservation], buckets: int
) -> dict[int, float]:
    grouped: dict[int, list[MarketObservation]] = {i: [] for i in range(buckets)}
    for obs in observations:
        grouped[assign_bucket(obs.price_at_tau, buckets)].append(obs)
    signs: dict[int, float] = {}
    for idx, items in grouped.items():
        if len(items) < MIN_BUCKET_SAMPLES:
            continue
        mean_price = sum(o.price_at_tau for o in items) / len(items)
        realized = sum(o.market.outcome_yes for o in items) / len(items)
        signs[idx] = mean_price - realized
    return signs


def h2_time_split_pass(
    observations: Sequence[MarketObservation],
    qualifying: Sequence[int],
    buckets: int,
    round_trip_cost_pct: float,
) -> bool:
    if not qualifying or len(observations) < 2 * MIN_BUCKET_SAMPLES:
        return False
    ordered = sorted(observations, key=lambda o: o.market.closed_time)
    mid = len(ordered) // 2
    older = ordered[:mid]
    newer = ordered[mid:]
    cost = round_trip_cost_pct / 100.0
    older_edges = _bucket_edge_sign_map(older, buckets)
    newer_edges = _bucket_edge_sign_map(newer, buckets)
    for idx in qualifying:
        if idx not in older_edges or idx not in newer_edges:
            continue
        o_edge, n_edge = older_edges[idx], newer_edges[idx]
        if o_edge == 0.0 or n_edge == 0.0:
            continue
        if (o_edge > 0) != (n_edge > 0):
            continue
        if abs(o_edge) > cost and abs(n_edge) > cost:
            return True
    return False


def h2_category_exclusion_pass(
    observations: Sequence[MarketObservation],
    buckets: int,
    round_trip_cost_pct: float,
) -> bool:
    if not observations:
        return False
    counts: dict[str, int] = {}
    for obs in observations:
        counts[obs.market.category_group] = counts.get(obs.market.category_group, 0) + 1
    if not counts:
        return False
    largest = max(counts, key=counts.get)
    if len(counts) < 2:
        return False
    filtered = [o for o in observations if o.market.category_group != largest]
    if len(filtered) < MIN_BUCKET_SAMPLES * 2:
        return False
    rebucketed = compute_calibration_buckets(filtered, buckets, round_trip_cost_pct)
    return len(_qualifying_indices(rebucketed)) > 0


def is_single_category_only(observations: Sequence[MarketObservation]) -> bool:
    groups = {o.market.category_group for o in observations}
    return len(groups) <= 1


def analyze_lead_time(
    observations: Sequence[MarketObservation],
    lead_hours: int,
    config: ProbeConfig,
) -> LeadTimeResult:
    buckets = compute_calibration_buckets(observations, config.buckets, config.round_trip_cost_pct)
    qualifying = _qualifying_indices(buckets)
    h1 = len(qualifying) > 0
    h2_time = h2_time_split_pass(
        observations, qualifying, config.buckets, config.round_trip_cost_pct
    )
    h2_cat = h2_category_exclusion_pass(observations, config.buckets, config.round_trip_cost_pct)
    h2 = h2_time and h2_cat
    single_cat = is_single_category_only(observations)
    counts: dict[str, int] = {}
    for obs in observations:
        counts[obs.market.category_group] = counts.get(obs.market.category_group, 0) + 1
    dominant = max(counts, key=counts.get) if counts else None
    return LeadTimeResult(
        lead_hours=lead_hours,
        observations=len(observations),
        buckets=buckets,
        qualifying_bucket_indices=qualifying,
        h1_pass=h1,
        h2_time_split_pass=h2_time,
        h2_category_exclusion_pass=h2_cat,
        h2_pass=h2,
        single_category_only=single_cat,
        dominant_category=dominant,
    )


def decide_verdict(
    audit: DataAudit, lead_results: Sequence[LeadTimeResult]
) -> tuple[str, str, tuple[str, ...]]:
    if audit.blocked:
        return (BLOCKED_ON_DATA, BLOCKED_ON_DATA, (audit.blocked_reason or "data gate failed",))

    reasons: list[str] = []
    any_h1 = any(r.h1_pass for r in lead_results)
    all_has_pulse = all(
        r.h1_pass and r.h2_pass and not r.single_category_only for r in lead_results
    )
    any_weak = any(r.h1_pass and (not r.h2_pass or r.single_category_only) for r in lead_results)

    for result in lead_results:
        reasons.append(
            f"τ={result.lead_hours}h: n={result.observations}, H1={'Y' if result.h1_pass else 'n'}, "
            f"H2_time={'Y' if result.h2_time_split_pass else 'n'}, "
            f"H2_cat={'Y' if result.h2_category_exclusion_pass else 'n'}, "
            f"qualifying_buckets={list(result.qualifying_bucket_indices)}"
        )
        if result.single_category_only and result.h1_pass:
            reasons.append(
                f"τ={result.lead_hours}h: edge confined to single category "
                f"({result.dominant_category}) — cannot be HAS_PULSE"
            )

    if all_has_pulse and lead_results:
        return (
            "OK",
            HAS_PULSE,
            tuple(reasons + ["H1 and H2 pass for all lead times; multi-category"]),
        )
    if any_h1 or any_weak:
        return (
            "OK",
            WEAK_EDGE,
            tuple(reasons + ["H1 passed but H2 and/or category breadth failed"]),
        )
    return ("OK", NO_PULSE, tuple(reasons + ["no tradeable miscalibration beyond round-trip cost"]))


async def fetch_gamma_markets_page(
    session: aiohttp.ClientSession,
    *,
    start: datetime,
    end: datetime,
    offset: int,
    limit: int = 100,
) -> list[dict[str, object]]:
    params = {
        "closed": "true",
        "uma_resolution_status": "resolved",
        "limit": str(limit),
        "offset": str(offset),
        "end_date_min": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date_max": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "order": "endDate",
        "ascending": "false",
    }
    async with session.get(f"{GAMMA_BASE}/markets", params=params) as response:
        response.raise_for_status()
        payload = await response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        return data if isinstance(data, list) else []
    return []


async def pull_resolved_markets(
    session: aiohttp.ClientSession,
    config: ProbeConfig,
) -> tuple[list[ResolvedMarket], DataAudit]:
    logger = get_logger("probe.polymarket")
    start = _parse_dt(config.start)
    end = _parse_dt(config.end)
    exclusions: dict[str, int] = {}
    disputed_flagged = 0
    kept: list[ResolvedMarket] = []
    total = 0
    offset = 0

    while True:
        try:
            batch = await fetch_gamma_markets_page(session, start=start, end=end, offset=offset)
        except Exception as exc:  # noqa: BLE001
            logger.warning("gamma fetch failed at offset %d (%s)", offset, type(exc).__name__)
            break
        if not batch:
            break
        for raw in batch:
            if not isinstance(raw, dict):
                continue
            total += 1
            question = str(raw.get("question") or "")
            description = str(raw.get("description") or "")
            if is_disputed_market(question, description):
                disputed_flagged += 1
                exclusions["disputed_resolution"] = exclusions.get("disputed_resolution", 0) + 1
                continue
            market, reason = classify_raw_market(raw)
            if market is None:
                exclusions[reason] = exclusions.get(reason, 0) + 1
                continue
            liq_reason = filter_for_liquidity(market, config.min_liquidity)
            if liq_reason:
                exclusions[liq_reason] = exclusions.get(liq_reason, 0) + 1
                continue
            kept.append(market)
        logger.info("gamma offset %d: batch=%d kept=%d", offset, len(batch), len(kept))
        if len(batch) < 100:
            break
        offset += 100
        await asyncio.sleep(0.12)

    category_mix: dict[str, int] = {}
    for market in kept:
        category_mix[market.category_group] = category_mix.get(market.category_group, 0) + 1

    audit = DataAudit(
        total_pulled=total,
        exclusions=exclusions,
        disputed_flagged=disputed_flagged,
        with_price_by_lead={},
        usable_for_edge=0,
        category_mix=category_mix,
        blocked=False,
        blocked_reason=None,
    )
    return kept, audit


def write_market_cache(path: Path, markets: Sequence[ResolvedMarket]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for market in markets:
            payload = asdict(market)
            payload["closed_time"] = market.closed_time.isoformat()
            handle.write(json.dumps(payload) + "\n")


def load_market_cache(path: Path) -> list[ResolvedMarket]:
    markets: list[ResolvedMarket] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            markets.append(
                ResolvedMarket(
                    market_id=raw["market_id"],
                    question=raw["question"],
                    condition_id=raw["condition_id"],
                    yes_token_id=raw["yes_token_id"],
                    category=raw["category"],
                    category_group=raw["category_group"],
                    volume_usd=float(raw["volume_usd"]),
                    closed_time=_parse_dt(raw["closed_time"]),
                    outcome_yes=int(raw["outcome_yes"]),
                    disputed_flag=bool(raw.get("disputed_flag", False)),
                    slug=raw.get("slug", ""),
                )
            )
    return markets


async def fetch_price_history(
    session: aiohttp.ClientSession, token_id: str
) -> list[tuple[int, float]]:
    params = {
        "market": token_id,
        "interval": "max",
        "fidelity": str(CLOB_MIN_FIDELITY_MINUTES),
    }
    async with session.get(PRICES_HISTORY_URL, params=params) as response:
        response.raise_for_status()
        payload = await response.json()
    history = payload.get("history") if isinstance(payload, dict) else None
    if not isinstance(history, list):
        return []
    out: list[tuple[int, float]] = []
    for point in history:
        if isinstance(point, dict) and "t" in point and "p" in point:
            out.append((int(point["t"]), float(point["p"])))
    out.sort(key=lambda x: x[0])
    return out


def price_at_lead(
    history: Sequence[tuple[int, float]], close_ts: int, lead_hours: int
) -> float | None:
    target_ts = close_ts - lead_hours * 3600
    candidates = [(t, p) for t, p in history if t <= target_ts]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


async def enrich_prices(
    session: aiohttp.ClientSession,
    markets: Sequence[ResolvedMarket],
    lead_hours: Sequence[int],
    *,
    max_fetch: int | None,
) -> tuple[dict[tuple[str, int], float], dict[str, int]]:
    logger = get_logger("probe.polymarket")
    sem = asyncio.Semaphore(PRICE_FETCH_CONCURRENCY)
    prices: dict[tuple[str, int], float] = {}
    skip_reasons: dict[str, int] = {}
    targets = list(markets[:max_fetch] if max_fetch else markets)

    async def one_market(market: ResolvedMarket) -> None:
        async with sem:
            try:
                history = await fetch_price_history(session, market.yes_token_id)
            except Exception:  # noqa: BLE001
                skip_reasons["price_fetch_error"] = skip_reasons.get("price_fetch_error", 0) + 1
                return
            if not history:
                skip_reasons["no_price_history"] = skip_reasons.get("no_price_history", 0) + 1
                return
            close_ts = int(market.closed_time.timestamp())
            for lead in lead_hours:
                price = price_at_lead(history, close_ts, lead)
                if price is None:
                    skip_reasons[f"no_price_at_{lead}h"] = (
                        skip_reasons.get(f"no_price_at_{lead}h", 0) + 1
                    )
                    continue
                prices[(market.market_id, lead)] = price
            await asyncio.sleep(0.05)

    await asyncio.gather(*(one_market(m) for m in targets))
    logger.info("price enrichment done: %d market-lead prices, skips=%s", len(prices), skip_reasons)
    return prices, skip_reasons


def default_cache_path(config: ProbeConfig) -> Path:
    start_tag = _parse_dt(config.start).strftime("%Y%m%d")
    end_tag = _parse_dt(config.end).strftime("%Y%m%d")
    if config.cache_file is not None:
        return config.cache_file
    return Path(f"data/polymarket/resolved_markets_{start_tag}_{end_tag}.jsonl")


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.polymarket")
    cache_path = default_cache_path(config)

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=60, connect=15),
        headers={"Accept": "application/json", "User-Agent": "crypto-agent-probe/1.0"},
    ) as session:
        if cache_path.is_file():
            logger.info("loading cached markets from %s", cache_path)
            markets = load_market_cache(cache_path)
            audit_partial = DataAudit(
                total_pulled=len(markets),
                exclusions={},
                disputed_flagged=0,
                with_price_by_lead={},
                usable_for_edge=0,
                category_mix={},
                blocked=False,
                blocked_reason=None,
            )
            for market in markets:
                audit_partial.category_mix[market.category_group] = (
                    audit_partial.category_mix.get(market.category_group, 0) + 1
                )
        else:
            markets, audit_partial = await pull_resolved_markets(session, config)
            write_market_cache(cache_path, markets)

        prices, price_skips = await enrich_prices(
            session,
            markets,
            config.lead_hours,
            max_fetch=config.max_price_fetch,
        )

    with_price_by_lead: dict[str, int] = {}
    for lead in config.lead_hours:
        count = sum(1 for m in markets if (m.market_id, lead) in prices)
        with_price_by_lead[f"{lead}h"] = count

    counts_per_lead = list(with_price_by_lead.values())
    min_with_price = min(counts_per_lead) if counts_per_lead else 0
    blocked = min_with_price < config.min_markets
    blocked_reason = (
        f"only {min_with_price} markets have price at all lead times "
        f"(need >= {config.min_markets}; per-τ counts={with_price_by_lead}); "
        f"price_skips={price_skips}"
        if blocked
        else None
    )

    exclusions = dict(audit_partial.exclusions)
    for reason, count in price_skips.items():
        exclusions[reason] = exclusions.get(reason, 0) + count

    data_audit = DataAudit(
        total_pulled=audit_partial.total_pulled,
        exclusions=exclusions,
        disputed_flagged=audit_partial.disputed_flagged,
        with_price_by_lead=with_price_by_lead,
        usable_for_edge=min_with_price,
        category_mix=audit_partial.category_mix,
        blocked=blocked,
        blocked_reason=blocked_reason,
    )

    lead_results: list[LeadTimeResult] = []
    for lead in config.lead_hours:
        observations = build_observations(markets, prices, lead)
        if observations:
            lead_results.append(analyze_lead_time(observations, lead, config))

    status, verdict, reasons = decide_verdict(data_audit, lead_results)
    return ProbeReport(
        config={
            "start": config.start,
            "end": config.end,
            "lead_hours": list(config.lead_hours),
            "buckets": config.buckets,
            "min_markets": config.min_markets,
            "round_trip_cost_pct": config.round_trip_cost_pct,
            "min_liquidity": config.min_liquidity,
            "cache_file": str(cache_path),
        },
        data_audit=data_audit,
        lead_results=tuple(lead_results),
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = ["# Polymarket Calibration / Favorite-Longshot Probe — Report", ""]
    lines.append(f"**Verdict:** **{report.verdict}**")
    lines.append("**Script:** `scripts/probe_polymarket_calibration.py`")
    lines.append("**Framing:** probability mispricing on resolved binary markets (read-only).")
    lines.append("")
    lines.append("## Cost assumption")
    lines.append(f"- {report.cost_assumption_note}")
    lines.append(f"- Applied round-trip cost: **{report.config['round_trip_cost_pct']}%**")
    lines.append("")
    a = report.data_audit
    lines.append("## STEP 0 — Data feasibility")
    lines.append(f"- Total pulled / cached: {a.total_pulled}")
    lines.append(f"- With price by lead time: {a.with_price_by_lead}")
    lines.append(f"- Usable for edge (min across τ): {a.usable_for_edge}")
    lines.append(f"- Exclusions: {a.exclusions}")
    lines.append(f"- Disputed/oracle-flagged (excluded): {a.disputed_flagged}")
    lines.append(f"- Category mix: {a.category_mix}")
    lines.append(f"- Blocked: {a.blocked}{f' — {a.blocked_reason}' if a.blocked_reason else ''}")
    lines.append("")

    for result in report.lead_results:
        lines.append(f"## STEP 1 — Calibration at τ = {result.lead_hours}h")
        lines.append(f"- Observations: {result.observations}")
        lines.append(
            f"- H1: {'PASS' if result.h1_pass else 'FAIL'} | H2 time: "
            f"{'PASS' if result.h2_time_split_pass else 'FAIL'} | H2 category: "
            f"{'PASS' if result.h2_category_exclusion_pass else 'FAIL'}"
        )
        lines.append(f"- Qualifying buckets: {list(result.qualifying_bucket_indices)}")
        lines.append("")
        lines.append(
            "| Bucket | n | mean(p) | freq | edge | net_edge | p_raw | p_adj | sig | trade |"
        )
        lines.append(
            "|--------|---|---------|------|------|----------|-------|-------|-----|-------|"
        )
        for b in result.buckets:
            lo = f"[{b.price_low:.1f},{b.price_high:.1f})"
            lines.append(
                f"| {lo} | {b.n} | {b.mean_price:.3f} | {b.realized_freq:.3f} | "
                f"{b.edge:+.3f} | {b.net_edge:+.3f} | {b.binom_p_raw:.4f} | "
                f"{b.binom_p_adj:.4f} | {'Y' if b.significant else 'n'} | "
                f"{'Y' if b.tradeable else 'n'} |"
            )
        lines.append("")

    lines.append("## Reasons")
    for reason in report.reasons:
        lines.append(f"- {reason}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--lead-hours", nargs="+", type=int, default=list(DEFAULT_LEAD_HOURS))
    parser.add_argument("--buckets", type=int, default=DEFAULT_BUCKETS)
    parser.add_argument("--min-markets", type=int, default=MIN_MARKETS)
    parser.add_argument("--round-trip-cost-pct", type=float, default=DEFAULT_ROUND_TRIP_COST_PCT)
    parser.add_argument("--min-liquidity", type=float, default=DEFAULT_MIN_LIQUIDITY)
    parser.add_argument(
        "--output-dir", default=str(Path("research/rbi_loop/polymarket-calibration-v0"))
    )
    parser.add_argument("--cache-file", default="")
    parser.add_argument(
        "--max-price-fetch",
        type=int,
        default=0,
        help="Cap markets for price-history fetch (0 = all). Useful for smoke runs.",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    cache = Path(args.cache_file) if args.cache_file else None
    config = ProbeConfig(
        start=args.start,
        end=args.end,
        lead_hours=tuple(args.lead_hours),
        buckets=args.buckets,
        min_markets=args.min_markets,
        round_trip_cost_pct=args.round_trip_cost_pct,
        min_liquidity=args.min_liquidity,
        cache_file=cache,
        max_price_fetch=args.max_price_fetch if args.max_price_fetch > 0 else None,
    )
    report = await run_probe(config)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": report.config,
        "data_audit": asdict(report.data_audit),
        "lead_results": [
            {
                **asdict(r),
                "buckets": [asdict(b) for b in r.buckets],
            }
            for r in report.lead_results
        ],
        "status": report.status,
        "verdict": report.verdict,
        "reasons": list(report.reasons),
        "cost_assumption_note": report.cost_assumption_note,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    (out_dir / "probe_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report_md = render_report(report)
    (out_dir / "probe_report.md").write_text(report_md + "\n", encoding="utf-8")

    print(report_md)
    print(f"\nVERDICT: {report.verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
