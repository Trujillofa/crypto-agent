from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from src.db import close_pool, init_pool
from src.portfolio.manager import PortfolioManager
from src.portfolio.models import PortfolioSummary


@dataclass(frozen=True)
class PaperAgentSpec:
    agent_id: str
    service: str
    config_path: str
    symbols: tuple[str, ...]
    timeframe: str


@dataclass(frozen=True)
class EventSummary:
    signal_count: int
    order_filled_count: int
    risk_check_failed_count: int
    last_signal_at: str | None
    last_order_at: str | None
    signals_by_symbol: dict[str, int] = field(default_factory=dict)
    orders_by_symbol: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskStateSummary:
    kill_switch_triggered: bool
    daily_pnl: float
    peak_balance: float
    open_position_keys: int
    active_breakers: list[str]
    updated_at: str | None


@dataclass(frozen=True)
class PaperAgentReport:
    agent: PaperAgentSpec
    day: str
    events: EventSummary
    risk: RiskStateSummary
    daily_realized_pnl: float
    daily_closed_trades: int
    daily_win_rate: float
    portfolio: PortfolioSummary


@dataclass(frozen=True)
class PaperValidationReport:
    generated_at: str
    day: str
    agents: list[PaperAgentReport]


DEFAULT_PAPER_AGENTS: tuple[PaperAgentSpec, ...] = (
    PaperAgentSpec(
        agent_id="sol-trend-pullback-sparse",
        service="agent_sol_sparse",
        config_path="config/settings.sol_trend_pullback_sparse.yaml",
        symbols=("SOLUSDT",),
        timeframe="4h",
    ),
    PaperAgentSpec(
        agent_id="sentiment-macro-bot",
        service="agent_sentiment_macro",
        config_path="config/settings.sentiment_macro.yaml",
        symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        timeframe="1h",
    ),
    PaperAgentSpec(
        agent_id="avax-4h-ma",
        service="agent_avax",
        config_path="config/settings.avax_4h_ma.yaml",
        symbols=("AVAXUSDT",),
        timeframe="4h",
    ),
)


def default_risk_state_path(agent_id: str, data_dir: Path = Path("data")) -> Path:
    if agent_id == "default":
        return data_dir / "risk_state.json"
    return data_dir / f"risk_state_{agent_id}.json"


def default_event_log_path(agent_id: str, data_dir: Path = Path("data")) -> Path:
    return data_dir / f"event_log_{agent_id}.jsonl"


def parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def summarize_event_log(path: Path, day: date) -> EventSummary:
    signal_count = 0
    order_filled_count = 0
    risk_check_failed_count = 0
    last_signal_at: str | None = None
    last_order_at: str | None = None
    signals_by_symbol: dict[str, int] = {}
    orders_by_symbol: dict[str, int] = {}
    start = _day_start(day)
    end = _day_start(day) + timedelta(days=1)

    if not path.exists():
        return EventSummary(
            signal_count=0,
            order_filled_count=0,
            risk_check_failed_count=0,
            last_signal_at=None,
            last_order_at=None,
        )

    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_ts = parse_iso_timestamp(payload.get("ts"))
            if event_ts is None or event_ts < start or event_ts >= end:
                continue

            event_type = payload.get("type")
            event_payload = payload.get("payload") or {}
            symbol = str(event_payload.get("symbol", "unknown"))

            if event_type == "signal_received":
                signal_count += 1
                last_signal_at = event_ts.isoformat()
                signals_by_symbol[symbol] = signals_by_symbol.get(symbol, 0) + 1
            elif event_type == "order_filled":
                order_filled_count += 1
                last_order_at = event_ts.isoformat()
                orders_by_symbol[symbol] = orders_by_symbol.get(symbol, 0) + 1
            elif event_type == "risk_check_failed":
                risk_check_failed_count += 1

    return EventSummary(
        signal_count=signal_count,
        order_filled_count=order_filled_count,
        risk_check_failed_count=risk_check_failed_count,
        last_signal_at=last_signal_at,
        last_order_at=last_order_at,
        signals_by_symbol=signals_by_symbol,
        orders_by_symbol=orders_by_symbol,
    )


def load_risk_state(path: Path) -> RiskStateSummary:
    if not path.exists():
        return RiskStateSummary(
            kill_switch_triggered=False,
            daily_pnl=0.0,
            peak_balance=0.0,
            open_position_keys=0,
            active_breakers=[],
            updated_at=None,
        )

    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    breakers = payload.get("circuit_breakers") or {}
    active_breakers = sorted(name for name, active in breakers.items() if active)

    return RiskStateSummary(
        kill_switch_triggered=bool(payload.get("kill_switch_triggered", False)),
        daily_pnl=float(payload.get("daily_pnl", 0.0) or 0.0),
        peak_balance=float(payload.get("peak_balance", 0.0) or 0.0),
        open_position_keys=len(payload.get("positions") or {}),
        active_breakers=active_breakers,
        updated_at=payload.get("updated_at"),
    )


async def collect_agent_report(
    spec: PaperAgentSpec,
    day: date,
    *,
    db_config: dict[str, object],
    data_dir: Path = Path("data"),
) -> PaperAgentReport:
    events = summarize_event_log(default_event_log_path(spec.agent_id, data_dir), day)
    risk = load_risk_state(default_risk_state_path(spec.agent_id, data_dir))

    await init_pool(db_config)
    manager = PortfolioManager(db_config, agent_id=spec.agent_id)
    async with manager:
        daily_realized_pnl, daily_closed_trades, daily_win_rate = await manager.get_daily_stats(day)
        portfolio = await manager.get_portfolio_summary()

    return PaperAgentReport(
        agent=spec,
        day=day.isoformat(),
        events=events,
        risk=risk,
        daily_realized_pnl=daily_realized_pnl,
        daily_closed_trades=daily_closed_trades,
        daily_win_rate=daily_win_rate,
        portfolio=portfolio,
    )


async def collect_report(
    *,
    db_config: dict[str, object],
    day: date,
    agents: tuple[PaperAgentSpec, ...] = DEFAULT_PAPER_AGENTS,
    data_dir: Path = Path("data"),
) -> PaperValidationReport:
    try:
        reports: list[PaperAgentReport] = []
        for spec in agents:
            reports.append(
                await collect_agent_report(
                    spec,
                    day,
                    db_config=db_config,
                    data_dir=data_dir,
                )
            )
        return PaperValidationReport(
            generated_at=datetime.now(UTC).isoformat(),
            day=day.isoformat(),
            agents=reports,
        )
    finally:
        await close_pool()


def report_to_json(report: PaperValidationReport) -> str:
    return json.dumps(asdict(report), indent=2, default=_json_default)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def render_markdown(report: PaperValidationReport) -> str:
    lines: list[str] = []
    lines.append("# Daily Paper Validation Report")
    lines.append("")
    lines.append(f"- Generated: {report.generated_at}")
    lines.append(f"- Day: `{report.day}`")
    lines.append("")

    total_daily_pnl = sum(agent.daily_realized_pnl for agent in report.agents)
    total_daily_trades = sum(agent.daily_closed_trades for agent in report.agents)
    total_signals = sum(agent.events.signal_count for agent in report.agents)
    total_orders = sum(agent.events.order_filled_count for agent in report.agents)
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- Daily realized PnL: `{total_daily_pnl:.2f} USDT`")
    lines.append(f"- Daily closed trades: `{total_daily_trades}`")
    lines.append(f"- Daily signals: `{total_signals}`")
    lines.append(f"- Daily order fills: `{total_orders}`")
    lines.append("")

    for agent in report.agents:
        lines.append(f"## {agent.agent.service}")
        lines.append("")
        lines.append(f"- Agent ID: `{agent.agent.agent_id}`")
        lines.append(f"- Config: `{agent.agent.config_path}`")
        lines.append(
            f"- Symbols/Timeframe: `{', '.join(agent.agent.symbols)}` / `{agent.agent.timeframe}`"
        )
        lines.append(f"- Daily realized PnL: `{agent.daily_realized_pnl:.2f} USDT`")
        lines.append(f"- Daily closed trades: `{agent.daily_closed_trades}`")
        lines.append(f"- Daily win rate: `{agent.daily_win_rate:.2f}%`")
        lines.append(f"- Daily signals: `{agent.events.signal_count}`")
        lines.append(f"- Daily order fills: `{agent.events.order_filled_count}`")
        lines.append(f"- Daily risk check failures: `{agent.events.risk_check_failed_count}`")
        lines.append(f"- Last signal: `{agent.events.last_signal_at or 'none'}`")
        lines.append(f"- Last order fill: `{agent.events.last_order_at or 'none'}`")
        lines.append(f"- Open positions: `{agent.portfolio.open_positions}`")
        lines.append(f"- Total realized PnL: `{agent.portfolio.total_realized_pnl:.2f} USDT`")
        lines.append(f"- Total win rate: `{agent.portfolio.win_rate:.2f}%`")
        lines.append(f"- Kill switch: `{agent.risk.kill_switch_triggered}`")
        lines.append(
            f"- Active breakers: `{', '.join(agent.risk.active_breakers) if agent.risk.active_breakers else 'none'}`"
        )
        lines.append(f"- Risk-state daily PnL: `{agent.risk.daily_pnl:.2f} USDT`")
        lines.append(f"- Peak balance anchor: `{agent.risk.peak_balance:.2f} USDT`")
        lines.append(f"- Risk-state updated: `{agent.risk.updated_at or 'none'}`")
        if agent.events.signals_by_symbol:
            lines.append(f"- Signals by symbol: `{agent.events.signals_by_symbol}`")
        if agent.events.orders_by_symbol:
            lines.append(f"- Orders by symbol: `{agent.events.orders_by_symbol}`")
        lines.append("")

    return "\n".join(lines)
