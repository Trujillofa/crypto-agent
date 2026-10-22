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

# Gauge: the age of the newest PUBLISHED indicator bar, in seconds,
# measured from that bar's own close_time to the moment of publication.
#
# This is the guard against publishing a forming candle. A published bar
# whose close_time is still in the future is a bar that was written
# before it finished forming -- the defect that froze a partial 4h value
# in the table and could never be corrected, because the live path only
# ever writes the newest bar.
#
# For a correctly published bar this is a small positive number: the
# time between the bar closing and the computer getting to it. For a
# forming bar it is NEGATIVE. A negative value is not "old data", it is
# a bar that had not closed yet.
indicator_published_bar_age_seconds = Gauge(
    "indicator_published_bar_age_seconds",
    "Seconds between the published bar's close_time and the moment it was "
    "published. Negative means the bar was still forming when it was written.",
    ["symbol", "timeframe"],
)

# Gauge: the timestamp of the newest published indicator bar.
indicator_published_bar_timestamp_seconds = Gauge(
    "indicator_published_bar_timestamp_seconds",
    "Unix timestamp of the newest published indicator bar.",
    ["symbol", "timeframe"],
)

# Gauge: 1 if the newest row in the SOURCE ohlcv table is still forming.
#
# This is the ROOT CAUSE guard, and it sits upstream of the publication
# guard above. The original defect began in the ingest layer: the REST
# poller wrote the in-progress kline, so the source table itself held a
# bar that had not closed. The computer then inherited it.
#
# The computer skips forming rows when it publishes, so this defect is
# invisible in the indicators table -- but the source table is still
# wrong, and the next writer (or a backfill) may pick it up. Detecting it
# here catches the cause rather than the symptom.
#
# The data is already loaded (the computer reads close_time to decide
# which bar is closed), so this costs no extra query.
indicator_source_newest_bar_was_forming = Gauge(
    "indicator_source_newest_bar_was_forming",
    "1 if the newest row in the source ohlcv table has not closed yet, "
    "0 otherwise. 1 means the ingest layer wrote a forming candle.",
    ["symbol", "timeframe"],
)

# Gauge: how long the newest source row has been open, in seconds.
#
# This is what makes the "forming bar" alert usable: a bar is normally
# open for the whole of its period, so a FIXED threshold either fires
# constantly on 4h pairs or never fires on a stuck 1h pair. Comparing
# this against the bar's own period is exact -- a forming bar becomes
# anomalous precisely when it has been open for LONGER than its period,
# because by then it should have closed.
indicator_source_newest_bar_forming_for_seconds = Gauge(
    "indicator_source_newest_bar_forming_for_seconds",
    "Seconds the newest source ohlcv row has been open. Compare against "
    "the bar period: a value larger than the period means the bar should "
    "have closed already.",
    ["symbol", "timeframe"],
)

# Gauge: the timeframe's period in seconds.
#
# This exists so the staleness alert can be expressed in BAR PERIODS
# rather than in wall-clock seconds. A 1h pair's newest published bar is
# naturally up to ~1h old, and a 4h pair's is naturally up to ~4h old --
# a fixed "older than 1h" threshold would fire constantly for every 4h
# pair. Comparing age against the period makes the same threshold mean
# the same thing for every timeframe.
indicator_published_bar_period_seconds = Gauge(
    "indicator_published_bar_period_seconds",
    "The timeframe period in seconds, used to express staleness in bar periods.",
    ["symbol", "timeframe"],
)

# Gauge: 1 if the newest published bar was still forming at publication
# time. Kept as a separate 0/1 gauge so an alert can fire on it directly
# rather than on a sign comparison.
indicator_published_bar_was_forming = Gauge(
    "indicator_published_bar_was_forming",
    "1 if the newest published indicator bar was still forming when it was written, 0 otherwise.",
    ["symbol", "timeframe"],
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
        self.published_bar_age = indicator_published_bar_age_seconds
        self.published_bar_timestamp = indicator_published_bar_timestamp_seconds
        self.published_bar_was_forming = indicator_published_bar_was_forming
        self.published_bar_period = indicator_published_bar_period_seconds
        self.source_newest_bar_was_forming = indicator_source_newest_bar_was_forming
        self.source_newest_bar_forming_for = indicator_source_newest_bar_forming_for_seconds

    def start_computation_loop(self) -> None:
        """Mark computation loop as running."""
        self.running.set(1)

    def stop_computation_loop(self) -> None:
        """Mark computation loop as stopped."""
        self.running.set(0)
