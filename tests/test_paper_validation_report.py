from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.portfolio.models import PortfolioSummary
from src.utils.paper_validation_report import (
    DEFAULT_PAPER_AGENTS,
    EventSummary,
    PaperAgentReport,
    PaperValidationReport,
    RiskStateSummary,
    collect_agent_report,
    default_event_log_path,
    default_risk_state_path,
    load_risk_state,
    parse_iso_timestamp,
    render_markdown,
    summarize_event_log,
)


def test_default_paths_handle_default_and_named_agents() -> None:
    assert default_risk_state_path("default") == Path("data/risk_state.json")
    assert default_risk_state_path("agent-x") == Path("data/risk_state_agent-x.json")
    assert default_event_log_path("agent-x") == Path("data/event_log_agent-x.jsonl")


def test_parse_iso_timestamp_handles_z_suffix() -> None:
    parsed = parse_iso_timestamp("2026-03-19T21:00:00Z")

    assert parsed is not None
    assert parsed.isoformat() == "2026-03-19T21:00:00+00:00"


def test_summarize_event_log_counts_only_requested_day(tmp_path: Path) -> None:
    path = tmp_path / "event_log_agent.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "seq": 1,
                        "ts": "2026-03-18T23:59:59+00:00",
                        "type": "signal_received",
                        "agent_id": "agent",
                        "payload": {"symbol": "BTCUSDT"},
                    }
                ),
                json.dumps(
                    {
                        "seq": 2,
                        "ts": "2026-03-19T00:00:01+00:00",
                        "type": "signal_received",
                        "agent_id": "agent",
                        "payload": {"symbol": "BTCUSDT"},
                    }
                ),
                json.dumps(
                    {
                        "seq": 3,
                        "ts": "2026-03-19T00:10:00+00:00",
                        "type": "order_filled",
                        "agent_id": "agent",
                        "payload": {"symbol": "ETHUSDT"},
                    }
                ),
                json.dumps(
                    {
                        "seq": 4,
                        "ts": "2026-03-19T01:00:00+00:00",
                        "type": "risk_check_failed",
                        "agent_id": "agent",
                        "payload": {"symbol": "ETHUSDT"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_event_log(path, date(2026, 3, 19))

    assert summary.signal_count == 1
    assert summary.order_filled_count == 1
    assert summary.risk_check_failed_count == 1
    assert summary.signals_by_symbol == {"BTCUSDT": 1}
    assert summary.orders_by_symbol == {"ETHUSDT": 1}


def test_load_risk_state_returns_defaults_when_missing(tmp_path: Path) -> None:
    summary = load_risk_state(tmp_path / "missing.json")

    assert summary.kill_switch_triggered is False
    assert summary.active_breakers == []
    assert summary.updated_at is None


def test_load_risk_state_extracts_active_breakers(tmp_path: Path) -> None:
    state_path = tmp_path / "risk.json"
    state_path.write_text(
        json.dumps(
            {
                "positions": {"BTCUSDT:spot": {}},
                "daily_pnl": 12.5,
                "peak_balance": 10123.0,
                "kill_switch_triggered": True,
                "circuit_breakers": {
                    "drawdown": True,
                    "daily_loss": False,
                    "api_errors": True,
                },
                "updated_at": "2026-03-19T21:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    summary = load_risk_state(state_path)

    assert summary.kill_switch_triggered is True
    assert summary.open_position_keys == 1
    assert summary.active_breakers == ["api_errors", "drawdown"]


@pytest.mark.asyncio
async def test_collect_agent_report_aggregates_db_and_files(tmp_path: Path) -> None:
    spec = DEFAULT_PAPER_AGENTS[0]
    data_dir = tmp_path
    default_event_log_path(spec.agent_id, data_dir).write_text(
        json.dumps(
            {
                "seq": 1,
                "ts": "2026-03-19T05:00:00+00:00",
                "type": "signal_received",
                "agent_id": spec.agent_id,
                "payload": {"symbol": "SOLUSDT"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    default_risk_state_path(spec.agent_id, data_dir).write_text(
        json.dumps(
            {
                "positions": {},
                "daily_pnl": 7.25,
                "peak_balance": 10050.0,
                "kill_switch_triggered": False,
                "circuit_breakers": {"drawdown": False},
                "updated_at": "2026-03-19T21:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    async def _enter(self):  # type: ignore[no-untyped-def]
        return self

    async def _exit(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        return None

    with (
        patch("src.utils.paper_validation_report.init_pool", AsyncMock()),
        patch("src.utils.paper_validation_report.close_pool", AsyncMock()),
        patch("src.utils.paper_validation_report.PortfolioManager.__aenter__", _enter),
        patch("src.utils.paper_validation_report.PortfolioManager.__aexit__", _exit),
        patch(
            "src.utils.paper_validation_report.PortfolioManager.get_daily_stats",
            AsyncMock(return_value=(12.5, 3, 66.67)),
        ),
        patch(
            "src.utils.paper_validation_report.PortfolioManager.get_portfolio_summary",
            AsyncMock(
                return_value=PortfolioSummary(
                    total_positions=4,
                    open_positions=1,
                    closed_positions=3,
                    total_trades=3,
                    total_realized_pnl=25.0,
                    win_count=2,
                    loss_count=1,
                )
            ),
        ),
    ):
        report = await collect_agent_report(
            spec,
            date(2026, 3, 19),
            db_config={"host": "timescaledb"},
            data_dir=data_dir,
        )

    assert report.agent.agent_id == spec.agent_id
    assert report.events.signal_count == 1
    assert report.risk.daily_pnl == 7.25
    assert report.daily_closed_trades == 3
    assert report.portfolio.total_realized_pnl == 25.0


def test_render_markdown_contains_key_sections() -> None:
    report = PaperAgentReport(
        agent=DEFAULT_PAPER_AGENTS[2],
        day="2026-03-19",
        events=EventSummary(
            signal_count=2,
            order_filled_count=1,
            risk_check_failed_count=0,
            last_signal_at="2026-03-19T12:00:00+00:00",
            last_order_at="2026-03-19T13:00:00+00:00",
            signals_by_symbol={"AVAXUSDT": 2},
            orders_by_symbol={"AVAXUSDT": 1},
        ),
        risk=RiskStateSummary(
            kill_switch_triggered=False,
            daily_pnl=0.0,
            peak_balance=10000.0,
            open_position_keys=0,
            active_breakers=[],
            updated_at="2026-03-19T13:05:00+00:00",
        ),
        daily_realized_pnl=4.5,
        daily_closed_trades=1,
        daily_win_rate=100.0,
        portfolio=PortfolioSummary(
            total_positions=1,
            open_positions=0,
            closed_positions=1,
            total_trades=1,
            total_realized_pnl=4.5,
            win_count=1,
            loss_count=0,
        ),
    )

    markdown = render_markdown(
        PaperValidationReport(
            generated_at="2026-03-19T21:00:00+00:00",
            day="2026-03-19",
            agents=[report],
        )
    )

    assert "Daily Paper Validation Report" in markdown
    assert "agent_avax" in markdown
    assert "AVAXUSDT" in markdown
    assert "Daily realized PnL" in markdown
