#!/usr/bin/env python3
"""WFO Parameter Sweep — not a selection tool.

``param_grid`` was never applied to the backtest. Use
``scripts/experiment_autopilot.py`` for a fixed config, or
``scripts/run_config_search.py`` for gated search. Not a live-go.
"""

from __future__ import annotations

import sys

from src.backtest.research_safety import refuse_broken_param_sweep, refuse_live_go


def parse_backtest_output(stdout: str) -> dict[str, float]:
    """Kept for callers that only parse existing backtest stdout."""
    metrics: dict[str, float] = {}
    for line in stdout.splitlines():
        if "Total Trades:" in line:
            metrics["trades"] = float(int(line.split(":")[1]))
        if "Win Rate:" in line:
            metrics["win_rate"] = float(line.split(":")[1].strip("%")) / 100
        if "Sharpe:" in line:
            metrics["sharpe"] = float(line.split(":")[1])
    return metrics


async def wfo_sweep(*_args: object, **_kwargs: object) -> None:
    """Refuse: this script cannot rank a param grid honestly."""
    refuse_broken_param_sweep()


if __name__ == "__main__":
    refuse_live_go(argv=sys.argv[1:])
    refuse_broken_param_sweep()
