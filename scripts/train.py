from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.features.technical import compute_indicators


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute technical indicators from OHLCV CSV"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="CSV with columns: time, open, high, low, close, volume",
    )
    parser.add_argument(
        "--output", required=True, help="Output CSV with indicator columns"
    )
    args = parser.parse_args()

    data = pd.read_csv(args.input)
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(data.columns)):
        missing = required.difference(set(data.columns))
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    indicators = compute_indicators(data)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([indicators.__dict__]).to_csv(output, index=False)


if __name__ == "__main__":
    main()
