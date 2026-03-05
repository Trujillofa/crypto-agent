from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from src.main import load_settings

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str
    path: str


@dataclass(frozen=True)
class ConfigReport:
    config_path: Path
    findings: list[Finding]

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == Severity.WARNING]

    @property
    def infos(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == Severity.INFO]


def discover_config_paths(config_dir: Path = Path("config")) -> list[Path]:
    """Return all settings*.yaml files in config directory."""
    return sorted(config_dir.glob("settings*.yaml"))


def run_config_doctor(paths: Iterable[Path]) -> list[ConfigReport]:
    """Run static validation checks on a set of config files."""
    return [analyze_config(path) for path in sorted(paths)]


def analyze_config(config_path: Path) -> ConfigReport:
    findings: list[Finding] = []

    if not config_path.exists():
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="CONFIG_NOT_FOUND",
                message="Config file does not exist",
                path=str(config_path),
            )
        )
        return ConfigReport(config_path=config_path, findings=findings)

    raw = _load_yaml(config_path, findings)
    if not isinstance(raw, Mapping):
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="CONFIG_NOT_MAPPING",
                message="Config root must be a YAML mapping",
                path="root",
            )
        )
        return ConfigReport(config_path=config_path, findings=findings)

    # Reuse runtime parser to ensure this config is actually loadable by the agent.
    try:
        load_settings(config_path)
    except Exception as exc:  # noqa: BLE001
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="RUNTIME_LOAD_FAILED",
                message=f"load_settings failed: {exc}",
                path="root",
            )
        )

    _validate_core_fields(raw, findings)
    _validate_trading_execution(raw, findings)
    _validate_strategy(raw, findings)
    _validate_futures_consistency(raw, findings)
    _validate_optional_services(raw, findings)

    return ConfigReport(config_path=config_path, findings=findings)


def report_to_json(reports: Iterable[ConfigReport]) -> str:
    serializable: list[dict[str, Any]] = []
    for report in reports:
        serializable.append(
            {
                "config_path": str(report.config_path),
                "findings": [asdict(finding) for finding in report.findings],
            }
        )
    return json.dumps(serializable, indent=2)


def _load_yaml(config_path: Path, findings: list[Finding]) -> Mapping[str, object] | object:
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception as exc:  # noqa: BLE001
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="YAML_PARSE_FAILED",
                message=f"YAML parse failed: {exc}",
                path=str(config_path),
            )
        )
        return {}


def _validate_core_fields(root: Mapping[str, object], findings: list[Finding]) -> None:
    trading = _as_mapping(root.get("trading"))
    pairs = _as_str_list(trading.get("pairs"))
    if not pairs:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="TRADING_PAIRS_EMPTY",
                message="trading.pairs is empty",
                path="trading.pairs",
            )
        )

    timeframe = _as_str(trading.get("timeframe"), default="")
    if timeframe and timeframe not in _TIMEFRAME_SECONDS:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                code="TIMEFRAME_UNKNOWN",
                message=f"Unknown timeframe '{timeframe}', interval sanity checks are skipped",
                path="trading.timeframe",
            )
        )


def _validate_trading_execution(root: Mapping[str, object], findings: list[Finding]) -> None:
    mode = _as_str(root.get("mode"), default="paper").lower()
    trading_execution = _as_mapping(root.get("trading_execution"))

    enabled = _as_bool(trading_execution.get("enabled"), default=False)
    test_mode = _as_bool(trading_execution.get("test_mode"), default=True)
    order_size_usdt = _as_float(trading_execution.get("order_size_usdt"), default=0.0)

    if enabled and order_size_usdt <= 0:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="ORDER_SIZE_INVALID",
                message="trading_execution.order_size_usdt must be > 0 when enabled",
                path="trading_execution.order_size_usdt",
            )
        )

    if mode == "paper" and enabled and not test_mode:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="PAPER_WITH_LIVE_EXECUTION",
                message="mode=paper with trading_execution.test_mode=false risks live execution",
                path="trading_execution.test_mode",
            )
        )

    use_atr_sizing = _as_bool(trading_execution.get("use_atr_sizing"), default=False)
    if use_atr_sizing:
        atr_multiplier = _as_float(trading_execution.get("atr_multiplier"), default=0.0)
        risk_per_trade_pct = _as_float(trading_execution.get("risk_per_trade_pct"), default=0.0)

        if atr_multiplier <= 0:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="ATR_MULTIPLIER_INVALID",
                    message="atr_multiplier must be > 0 when use_atr_sizing=true",
                    path="trading_execution.atr_multiplier",
                )
            )

        if risk_per_trade_pct <= 0:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="RISK_PER_TRADE_INVALID",
                    message="risk_per_trade_pct must be > 0 when use_atr_sizing=true",
                    path="trading_execution.risk_per_trade_pct",
                )
            )
        elif risk_per_trade_pct > 0.05:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="RISK_PER_TRADE_HIGH",
                    message="risk_per_trade_pct above 5% is aggressive",
                    path="trading_execution.risk_per_trade_pct",
                )
            )


def _validate_strategy(root: Mapping[str, object], findings: list[Finding]) -> None:
    trading = _as_mapping(root.get("trading"))
    pairs = set(_as_str_list(trading.get("pairs")))

    strategy = _as_mapping(root.get("strategy"))
    strategies = _as_list_of_mappings(strategy.get("strategies"))
    strategy_count = len(strategies)

    aggregator = _as_mapping(strategy.get("aggregator"))
    _validate_aggregator(
        aggregator,
        strategy_count=strategy_count,
        findings=findings,
        path_prefix="strategy.aggregator",
    )

    per_symbol = _as_mapping(strategy.get("per_symbol_aggregator_config"))
    for symbol, raw_symbol_cfg in per_symbol.items():
        symbol_name = str(symbol).upper()
        if symbol_name not in pairs:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="PER_SYMBOL_UNUSED",
                    message=f"Per-symbol override for {symbol_name} is not in trading.pairs",
                    path=f"strategy.per_symbol_aggregator_config.{symbol_name}",
                )
            )

        symbol_cfg = _as_mapping(raw_symbol_cfg)
        _validate_aggregator(
            symbol_cfg,
            strategy_count=strategy_count,
            findings=findings,
            path_prefix=f"strategy.per_symbol_aggregator_config.{symbol_name}",
        )

    timeframe = _as_str(trading.get("timeframe"), default="")
    timeframe_seconds = _TIMEFRAME_SECONDS.get(timeframe)
    eval_seconds = _as_int(strategy.get("evaluation_interval_seconds"), default=0)
    if timeframe_seconds and eval_seconds > timeframe_seconds:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                code="EVAL_INTERVAL_SLOW",
                message=(
                    "evaluation_interval_seconds is greater than timeframe; signals may lag new candles"
                ),
                path="strategy.evaluation_interval_seconds",
            )
        )


def _validate_aggregator(
    aggregator: Mapping[str, object],
    strategy_count: int,
    findings: list[Finding],
    path_prefix: str,
) -> None:
    if strategy_count <= 0:
        return

    min_agreement = _as_int(aggregator.get("min_agreement"), default=1)
    sell_min_agreement = _as_int(aggregator.get("sell_min_agreement"), default=min_agreement)
    buy_threshold = _as_float(aggregator.get("buy_threshold"), default=0.5)
    buy_threshold_uptrend = _as_float(
        aggregator.get("buy_threshold_uptrend"),
        default=buy_threshold,
    )
    sell_threshold = _as_float(aggregator.get("sell_threshold"), default=-0.5)

    if min_agreement <= 0:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="MIN_AGREEMENT_INVALID",
                message="min_agreement must be >= 1",
                path=f"{path_prefix}.min_agreement",
            )
        )

    if min_agreement > strategy_count:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="MIN_AGREEMENT_IMPOSSIBLE",
                message=(
                    f"min_agreement={min_agreement} exceeds available strategies ({strategy_count})"
                ),
                path=f"{path_prefix}.min_agreement",
            )
        )

    if sell_min_agreement > strategy_count:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="SELL_MIN_AGREEMENT_IMPOSSIBLE",
                message=(
                    "sell_min_agreement exceeds available strategies "
                    f"({sell_min_agreement} > {strategy_count})"
                ),
                path=f"{path_prefix}.sell_min_agreement",
            )
        )

    max_score = float(strategy_count)
    if buy_threshold > max_score:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="BUY_THRESHOLD_IMPOSSIBLE",
                message=(
                    f"buy_threshold={buy_threshold:.3f} cannot be reached with "
                    f"{strategy_count} strategies (max score {max_score:.3f})"
                ),
                path=f"{path_prefix}.buy_threshold",
            )
        )

    if buy_threshold_uptrend > max_score:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="BUY_THRESHOLD_UPTREND_IMPOSSIBLE",
                message=(
                    f"buy_threshold_uptrend={buy_threshold_uptrend:.3f} cannot be reached with "
                    f"{strategy_count} strategies (max score {max_score:.3f})"
                ),
                path=f"{path_prefix}.buy_threshold_uptrend",
            )
        )

    if sell_threshold >= 0:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                code="SELL_THRESHOLD_NON_NEGATIVE",
                message="sell_threshold should usually be negative",
                path=f"{path_prefix}.sell_threshold",
            )
        )
    elif abs(sell_threshold) > max_score:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="SELL_THRESHOLD_IMPOSSIBLE",
                message=(
                    f"sell_threshold={sell_threshold:.3f} cannot be reached with "
                    f"{strategy_count} strategies (min score {-max_score:.3f})"
                ),
                path=f"{path_prefix}.sell_threshold",
            )
        )


def _validate_futures_consistency(root: Mapping[str, object], findings: list[Finding]) -> None:
    strategy = _as_mapping(root.get("strategy"))
    default_mode = _as_str(strategy.get("default_trading_mode"), default="spot")

    trading = _as_mapping(root.get("trading"))
    pairs = set(_as_str_list(trading.get("pairs")))

    futures = _as_mapping(root.get("futures"))
    futures_enabled = _as_bool(futures.get("enabled"), default=False)
    futures_symbols = set(_as_str_list(futures.get("symbols")))

    if default_mode == "futures" and not futures_enabled:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                code="FUTURES_MODE_DISABLED",
                message="strategy.default_trading_mode=futures but futures.enabled=false",
                path="futures.enabled",
            )
        )

    if default_mode != "futures" and futures_enabled:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                code="FUTURES_ENABLED_NOT_DEFAULT",
                message="futures.enabled=true but default_trading_mode is not 'futures'",
                path="strategy.default_trading_mode",
            )
        )

    extra_futures = sorted(futures_symbols - pairs)
    if extra_futures:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                code="FUTURES_SYMBOLS_NOT_TRADED",
                message=f"futures.symbols not present in trading.pairs: {', '.join(extra_futures)}",
                path="futures.symbols",
            )
        )


def _validate_optional_services(root: Mapping[str, object], findings: list[Finding]) -> None:
    telegram = _as_mapping(root.get("telegram"))
    if _as_bool(telegram.get("enabled"), default=False):
        bot_token = _as_str(telegram.get("bot_token"), default="")
        if not bot_token.strip():
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="TELEGRAM_TOKEN_EMPTY",
                    message="telegram.enabled=true but bot_token is empty (must come from env)",
                    path="telegram.bot_token",
                )
            )

    ai = _as_mapping(root.get("ai"))
    if _as_bool(ai.get("enabled"), default=False):
        api_key = _as_str(ai.get("api_key"), default="")
        if not api_key.strip():
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    code="AI_API_KEY_EMPTY",
                    message="ai.enabled=true but api_key is empty (must come from env)",
                    path="ai.api_key",
                )
            )


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_list_of_mappings(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return default


def _as_float(value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_int(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_str(value: object, default: str) -> str:
    if isinstance(value, str):
        return value
    return default
