#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.risk.manager import RiskManager


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/risk.yaml")
    parser.add_argument("--state", default="data/risk_state.json")
    parser.add_argument("--keep-counters", action="store_true")
    parser.add_argument("--keep-peak-balance", action="store_true")
    args = parser.parse_args()

    manager = RiskManager(config_path=Path(args.config), state_path=Path(args.state))
    before = manager.get_risk_summary()
    manager.clear_trading_blocks(
        reset_counters=not args.keep_counters,
        reset_peak_balance=not args.keep_peak_balance,
    )
    after = manager.get_risk_summary()

    print(json.dumps({"before": before, "after": after}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
