#!/usr/bin/env python3
"""Validate agent isolation configuration.

Checks for potential cross-agent contamination:
1. Unique agent_ids across all configs
2. No symbol overlap without timeframe distinction
3. Warns on risky configurations

Usage:
    python scripts/validate_agent_isolation.py
"""

import sys
from pathlib import Path

import yaml


def load_all_configs(config_dir: Path = Path("config")) -> dict[str, dict]:
    """Load all settings YAML files."""
    configs = {}

    for yaml_file in config_dir.glob("settings*.yaml"):
        try:
            with open(yaml_file, "r") as f:
                configs[yaml_file.name] = yaml.safe_load(f)
        except Exception as e:
            print(f"⚠️  Warning: Could not load {yaml_file}: {e}")

    return configs


def extract_agent_info(config: dict, filename: str) -> dict | None:
    """Extract agent_id and symbols from config."""
    agent_id = config.get("agent_id", "default")
    symbols = config.get("trading_pairs", [])
    timeframe = config.get("timeframe", "1h")

    return {
        "filename": filename,
        "agent_id": agent_id,
        "symbols": set(symbols),
        "timeframe": timeframe,
    }


def validate_isolation(configs: dict[str, dict]) -> tuple[bool, list[str]]:
    """Validate agent isolation.

    Returns:
        (is_valid, list_of_issues)
    """
    issues = []
    agents = []

    # Extract agent info
    for filename, config in configs.items():
        info = extract_agent_info(config, filename)
        if info:
            agents.append(info)

    # Check 1: Unique agent_ids
    agent_ids = [a["agent_id"] for a in agents]
    seen_ids = set()
    for agent in agents:
        agent_id = agent["agent_id"]
        if agent_id in seen_ids:
            issues.append(f"❌ DUPLICATE agent_id: '{agent_id}' appears in multiple configs")
        seen_ids.add(agent_id)

    # Check 2: Symbol overlap without timeframe distinction
    symbol_agents: dict[str, list[dict]] = {}
    for agent in agents:
        for symbol in agent["symbols"]:
            if symbol not in symbol_agents:
                symbol_agents[symbol] = []
            symbol_agents[symbol].append(agent)

    for symbol, agent_list in symbol_agents.items():
        if len(agent_list) > 1:
            # Check if timeframe is in agent_id
            agents_without_timeframe = []
            for agent in agent_list:
                agent_id = agent["agent_id"]
                timeframe = agent["timeframe"]
                # Check if agent_id includes timeframe (e.g., "sol_4h", "btc_1h")
                if timeframe not in agent_id and not any(
                    tf in agent_id for tf in ["1m", "5m", "15m", "1h", "4h", "1d"]
                ):
                    agents_without_timeframe.append(agent)

            if len(agents_without_timeframe) > 1:
                agent_names = [a["agent_id"] for a in agents_without_timeframe]
                issues.append(
                    f"⚠️  RISKY: Symbol '{symbol}' traded by multiple agents without "
                    f"timeframe distinction in agent_id: {agent_names}"
                )

    # Check 3: Warn on "default" agent_id
    for agent in agents:
        if agent["agent_id"] == "default":
            issues.append(
                f"⚠️  WARNING: {agent['filename']} uses 'default' agent_id. "
                f"Consider using descriptive agent_id for isolation."
            )

    is_valid = not any(i.startswith("❌") for i in issues)
    return is_valid, issues


def main():
    print("=" * 70)
    print("AGENT ISOLATION VALIDATION")
    print("=" * 70)

    configs = load_all_configs()

    if not configs:
        print("\n❌ No config files found in config/ directory")
        sys.exit(1)

    print(f"\nLoaded {len(configs)} config files:")
    for filename in sorted(configs.keys()):
        print(f"  - {filename}")

    is_valid, issues = validate_isolation(configs)

    print("\n" + "-" * 70)
    print("VALIDATION RESULTS")
    print("-" * 70)

    if not issues:
        print("\n✅ All isolation checks passed!")
    else:
        for issue in issues:
            print(f"\n{issue}")

    print("\n" + "=" * 70)

    if is_valid:
        print("✅ VALIDATION PASSED")
        print("\nAgent isolation is properly configured.")
        sys.exit(0)
    else:
        print("❌ VALIDATION FAILED")
        print("\nFix duplicate agent_ids before deploying.")
        sys.exit(1)


if __name__ == "__main__":
    main()
