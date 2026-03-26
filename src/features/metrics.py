from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Summary

# Counter for indicator computations
indicator_computations_total = Counter(
    "indicator_computations_total",
    "Total number of indicator computations",
    ["symbol", "status"],
)

# Gauge for latest indicator values
indicator_rsi = Gauge("indicator_rsi", "Latest RSI value", ["symbol", "period"])

indicator_macd = Gauge(
    "indicator_macd",
    "Latest MACD value",
    ["symbol", "component"],  # component: macd, signal, hist
)

indicator_atr = Gauge("indicator_atr", "Latest ATR value", ["symbol", "period"])

# Histogram for computation latency
computation_latency_seconds = Histogram(
    "indicator_computation_latency_seconds",
    "Indicator computation latency in seconds",
    ["symbol"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# Summary for successful computations
computation_duration_seconds = Summary(
    "indicator_computation_duration_seconds",
    "Summary of indicator computation durations",
    ["symbol"],
)

# Gauge for last successful computation time
last_computation_timestamp = Gauge(
    "indicator_last_computation_timestamp_seconds",
    "Unix timestamp of last successful indicator computation",
    ["symbol"],
)

# Counter for errors
computation_errors_total = Counter(
    "indicator_computation_errors_total",
    "Total number of indicator computation errors",
    ["symbol", "error_type"],
)

# Gauge for regime features NULL detection
regime_features_null = Gauge(
    "indicator_regime_features_null",
    "Whether regime features are NULL for the active pair (1=null, 0=ok)",
    ["symbol"],
)

# Gauge for computation loop status
computation_running = Gauge(
    "indicator_computation_running",
    "Whether the indicator computation loop is running (1=running, 0=stopped)",
)

# Counter for writes to database
indicator_writes_total = Counter(
    "indicator_writes_total",
    "Total number of indicator writes to database",
    ["symbol", "status"],
)

# Histogram for write latency
write_latency_seconds = Histogram(
    "indicator_write_latency_seconds",
    "Indicator write latency in seconds",
    ["symbol"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)


class IndicatorMetrics:
    """Container for indicator pipeline metrics."""

    def __init__(self) -> None:
        self.computations_total = indicator_computations_total
        self.rsi = indicator_rsi
        self.macd = indicator_macd
        self.atr = indicator_atr
        self.computation_latency = computation_latency_seconds
        self.computation_duration = computation_duration_seconds
        self.last_computation_time = last_computation_timestamp
        self.errors_total = computation_errors_total
        self.running = computation_running
        self.writes_total = indicator_writes_total
        self.write_latency = write_latency_seconds

    def start_computation_loop(self) -> None:
        """Mark computation loop as running."""
        self.running.set(1)

    def stop_computation_loop(self) -> None:
        """Mark computation loop as stopped."""
        self.running.set(0)
