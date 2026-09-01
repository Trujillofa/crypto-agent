"""WFO child-output parser must use scripts/run_backtest.py labels."""

from __future__ import annotations

from pathlib import Path

from scripts.run_wfo import parse_run_backtest_stdout

BACKTEST_SCRIPT = Path("scripts/run_backtest.py")


def _actual_run_backtest_results_block(*, trades: int, win_rate: float, sharpe: float) -> str:
    """Stdout block using the same print labels as scripts/run_backtest.py."""
    return (
        "\n" + "=" * 40 + "\n"
        "BACKTEST RESULTS\n" + "=" * 40 + "\n"
        f"Total Trades: {trades}\n"
        "Blocked BUY (session router): 0\n"
        "Blocked BUY (basis filter): 0\n"
        "Blocked BUY (cross-venue dislocation): 0\n"
        f"Win Rate:     {win_rate:.2f}%\n"
        "Total Return: $12.50 (1.25%)\n"
        "Max Drawdown: 3.00%\n"
        "Final Equity: $1012.50\n"
        f"Sharpe Ratio: {sharpe:.2f}\n"
        "Profit Factor: 1.50\n" + "=" * 40 + "\n"
    )


def test_run_backtest_emits_sharpe_ratio_label() -> None:
    source = BACKTEST_SCRIPT.read_text(encoding="utf-8")
    assert 'print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")' in source
    assert 'print(f"Total Trades: {result.total_trades}")' in source
    assert 'print(f"Win Rate:     {result.win_rate:.2f}%")' in source
    assert 'print(f"Sharpe: {result.sharpe_ratio:.2f}")' not in source


def test_wfo_parser_reads_actual_run_backtest_labels() -> None:
    stdout = _actual_run_backtest_results_block(trades=3, win_rate=50.00, sharpe=1.25)
    metrics = parse_run_backtest_stdout(stdout)
    assert metrics == {"trades": 3.0, "win_rate": 0.5, "sharpe": 1.25}


def test_legacy_sharpe_label_would_drop_ratio_line() -> None:
    """Reproduction: searching for 'Sharpe:' misses 'Sharpe Ratio:'."""
    stdout = _actual_run_backtest_results_block(trades=3, win_rate=50.00, sharpe=1.25)
    legacy: dict[str, float] = {}
    for line in stdout.splitlines():
        if "Total Trades:" in line:
            legacy["trades"] = float(int(line.split(":")[1]))
        elif "Win Rate:" in line:
            legacy["win_rate"] = float(line.split(":")[1].strip("%")) / 100.0
        elif "Sharpe:" in line:
            legacy["sharpe"] = float(line.split(":")[1])
    assert legacy == {"trades": 3.0, "win_rate": 0.5}
    assert "sharpe" not in legacy
    assert parse_run_backtest_stdout(stdout)["sharpe"] == 1.25
