from __future__ import annotations

import re
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

from prometheus_client import generate_latest

from src.utils.logger import get_logger


# Maximum allowed cardinality per metric
MAX_CARDINALITY = 100

# Allowed label values for specific labels
ALLOWED_SYMBOLS = {
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "ADAUSDT",
    "XRPUSDT",
    "DOTUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
}

ALLOWED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1d"}

ALLOWED_STREAMS = {"klines", "trades", "book", "mark_price", "funding"}

# Label value sanitization patterns
INVALID_LABEL_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


class LabelValidator:
    """Validates and sanitizes metric labels to prevent high cardinality."""

    def __init__(self) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._cardinality_cache: dict[str, set[str]] = {}
        self._warned_labels: set[str] = set()

    def sanitize_label_value(self, label_name: str, value: str) -> str:
        """Sanitize a label value to prevent high cardinality issues."""
        original_value = value

        # Truncate long values
        if len(value) > 64:
            value = value[:64]
            self._logger.warning(
                f"Label '{label_name}' value truncated: {original_value[:20]}..."
            )

        # Remove invalid characters
        value = INVALID_LABEL_CHARS.sub("_", value)

        # Validate against allowed sets for known labels
        if label_name == "symbol":
            value = self._validate_against_set("symbol", value, ALLOWED_SYMBOLS)
        elif label_name == "timeframe":
            value = self._validate_against_set("timeframe", value, ALLOWED_TIMEFRAMES)
        elif label_name == "stream":
            value = self._validate_against_set("stream", value, ALLOWED_STREAMS)

        return value

    def _validate_against_set(
        self, label_name: str, value: str, allowed_set: set[str]
    ) -> str:
        """Validate a label value against an allowed set."""
        if value not in allowed_set:
            if value not in self._warned_labels:
                self._logger.warning(
                    f"Label '{label_name}' has unknown value '{value}'. "
                    f"Allowed: {allowed_set}"
                )
                self._warned_labels.add(value)
            # Return a sanitized fallback
            return "unknown"
        return value

    def check_cardinality(
        self, metric_name: str, label_values: tuple[str, ...]
    ) -> bool:
        """Check if adding this label combination would exceed cardinality limits."""
        cache_key = f"{metric_name}:{label_values}"

        if metric_name not in self._cardinality_cache:
            self._cardinality_cache[metric_name] = set()

        unique_values = self._cardinality_cache[metric_name]

        if cache_key not in unique_values:
            if len(unique_values) >= MAX_CARDINALITY:
                if metric_name not in self._warned_labels:
                    self._logger.error(
                        f"Metric '{metric_name}' has exceeded max cardinality ({MAX_CARDINALITY}). "
                        f"Dropping new label combination to prevent Prometheus overload."
                    )
                    self._warned_labels.add(metric_name)
                return False
            unique_values.add(cache_key)

        return True


@dataclass(frozen=True)
class MetricKey:
    labels: tuple[tuple[str, str], ...]

    @staticmethod
    def from_labels(labels: dict[str, str]) -> "MetricKey":
        return MetricKey(tuple(sorted(labels.items())))

    def render(self) -> str:
        if not self.labels:
            return ""
        formatted = ",".join(f'{key}="{value}"' for key, value in self.labels)
        return f"{{{formatted}}}"


@dataclass
class Counter:
    name: str
    help_text: str
    label_names: tuple[str, ...]
    values: dict[MetricKey, int] = field(default_factory=dict)
    _validator: LabelValidator = field(default_factory=LabelValidator, repr=False)

    def with_labels(self, **labels: str) -> "Counter":
        if set(labels.keys()) != set(self.label_names):
            raise ValueError("Counter labels do not match")

        # Sanitize label values
        sanitized = {
            k: self._validator.sanitize_label_value(k, v) for k, v in labels.items()
        }

        # Check cardinality
        label_values = tuple(sanitized.values())
        if not self._validator.check_cardinality(self.name, label_values):
            return self  # Return self without incrementing if cardinality exceeded

        key = MetricKey.from_labels(sanitized)
        self.values.setdefault(key, 0)
        return self

    def inc(self, amount: int = 1, labels: dict[str, str] | None = None) -> None:
        labels = labels or {}

        # Sanitize label values
        sanitized = {
            k: self._validator.sanitize_label_value(k, v) for k, v in labels.items()
        }

        # Check cardinality
        label_values = tuple(sanitized.values())
        if not self._validator.check_cardinality(self.name, label_values):
            return  # Silently drop if cardinality exceeded

        key = MetricKey.from_labels(sanitized)
        self.values[key] = self.values.get(key, 0) + amount


@dataclass
class Gauge:
    name: str
    help_text: str
    label_names: tuple[str, ...]
    values: dict[MetricKey, float] = field(default_factory=dict)
    _validator: LabelValidator = field(default_factory=LabelValidator, repr=False)

    def with_labels(self, **labels: str) -> "Gauge":
        if set(labels.keys()) != set(self.label_names):
            raise ValueError("Gauge labels do not match")

        # Sanitize label values
        sanitized = {
            k: self._validator.sanitize_label_value(k, v) for k, v in labels.items()
        }

        # Check cardinality
        label_values = tuple(sanitized.values())
        if not self._validator.check_cardinality(self.name, label_values):
            return self  # Return self without setting if cardinality exceeded

        key = MetricKey.from_labels(sanitized)
        self.values.setdefault(key, 0.0)
        return self

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        labels = labels or {}

        # Sanitize label values
        sanitized = {
            k: self._validator.sanitize_label_value(k, v) for k, v in labels.items()
        }

        # Check cardinality
        label_values = tuple(sanitized.values())
        if not self._validator.check_cardinality(self.name, label_values):
            return  # Silently drop if cardinality exceeded

        key = MetricKey.from_labels(sanitized)
        self.values[key] = value


@dataclass
class MetricsRegistry:
    counters: list[Counter] = field(default_factory=list)
    gauges: list[Gauge] = field(default_factory=list)

    def render(self) -> str:
        lines: list[str] = []
        for counter in self.counters:
            lines.append(f"# HELP {counter.name} {counter.help_text}")
            lines.append(f"# TYPE {counter.name} counter")
            for key, value in counter.values.items():
                lines.append(f"{counter.name}{key.render()} {value}")
        for gauge in self.gauges:
            lines.append(f"# HELP {gauge.name} {gauge.help_text}")
            lines.append(f"# TYPE {gauge.name} gauge")
            for key, value in gauge.values.items():
                lines.append(f"{gauge.name}{key.render()} {value}")
        return "\n".join(lines)

    def get_cardinality_stats(self) -> dict[str, int]:
        """Get cardinality statistics for all metrics."""
        stats = {}
        for counter in self.counters:
            stats[counter.name] = len(counter.values)
        for gauge in self.gauges:
            stats[gauge.name] = len(gauge.values)
        return stats


@dataclass
class IngestMetrics:
    registry: MetricsRegistry = field(default_factory=MetricsRegistry)
    messages_total: Counter = field(
        default_factory=lambda: Counter(
            "ingest_messages_total",
            "Total number of ingest messages processed",
            ("symbol", "stream"),
        )
    )
    insert_latency_seconds: Gauge = field(
        default_factory=lambda: Gauge(
            "ingest_insert_latency_seconds",
            "Database insert latency in seconds",
            tuple(),
        )
    )
    last_open_time: Gauge = field(
        default_factory=lambda: Gauge(
            "ingest_last_open_time",
            "Last open time timestamp (seconds since epoch)",
            ("symbol",),
        )
    )
    errors_total: Counter = field(
        default_factory=lambda: Counter(
            "ingest_errors_total",
            "Total number of errors by type",
            ("error_type",),
        )
    )

    def __post_init__(self) -> None:
        self.registry.counters.append(self.messages_total)
        self.registry.gauges.append(self.insert_latency_seconds)
        self.registry.gauges.append(self.last_open_time)
        self.registry.counters.append(self.errors_total)


class MetricsServer:
    def __init__(self, registry: MetricsRegistry) -> None:
        self._registry = registry

    def start(self, port: int) -> Thread:
        registry = self._registry

        class MetricsHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in {"/metrics", "/health", "/ready", "/cardinality"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                if self.path == "/metrics":
                    custom_metrics = registry.render().encode("utf-8")
                    standard_metrics = generate_latest()
                    payload = custom_metrics + b"\n" + standard_metrics
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if self.path == "/cardinality":
                    stats = registry.get_cardinality_stats()
                    import json

                    payload = json.dumps(stats, indent=2).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        server = HTTPServer(("0.0.0.0", port), MetricsHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return thread
