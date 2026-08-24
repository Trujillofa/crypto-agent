"""Refuse live-go / promote flags on research (backtest / WFO) paths."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

FORBIDDEN_LIVE_CLI_FLAGS = frozenset(
    {
        "--live",
        "--live-go",
        "--live_go",
        "--promote",
        "--promote-live",
    }
)
FORBIDDEN_LIVE_FLAG_NAMES = frozenset({"live", "live_go", "promote", "promote_live"})
FORBIDDEN_LIVE_ENV = "CRYPTO_AGENT_LIVE_GO"

_REFUSAL = (
    "Backtest/WFO is not a live-go. Promote and live execution stay off on "
    "research paths. Paper→live is a separate human deploy, not a backtest flag."
)


class LiveGoRefused(ValueError):
    """Raised when a research path is asked to arm live trading."""


def refuse_live_go(
    argv: Sequence[str] | None = None,
    flags: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Raise if argv, kwargs, or env ask this research path to go live."""
    for arg in argv or ():
        key = arg.split("=", 1)[0]
        if key in FORBIDDEN_LIVE_CLI_FLAGS:
            raise LiveGoRefused(_REFUSAL)
    if flags:
        for name in FORBIDDEN_LIVE_FLAG_NAMES:
            if flags.get(name):
                raise LiveGoRefused(_REFUSAL)
    environ = os.environ if env is None else env
    raw = environ.get(FORBIDDEN_LIVE_ENV, "")
    if raw.strip().lower() in {"1", "true", "yes", "on"}:
        raise LiveGoRefused(_REFUSAL)


def refuse_broken_param_sweep() -> None:
    """``run_wfo_sweep.py`` never applies param_grid; it is not a selection tool."""
    raise RuntimeError(
        "scripts/run_wfo_sweep.py is not a selection tool: param_grid is never "
        "applied to the backtest. Use scripts/experiment_autopilot.py for a "
        "fixed config, or scripts/run_config_search.py for gated search. "
        "This is not a live-go."
    )
