"""Tests for ingest/metrics.py."""

from __future__ import annotations

import pytest

from src.ingest.metrics import (
    Counter,
    Gauge,
    LabelValidator,
    MAX_CARDINALITY,
    MetricKey,
    MetricsRegistry,
)


class TestLabelValidator:
    """Test suite for LabelValidator."""

    def test_sanitize_valid_symbol(self) -> None:
        """Test sanitizing valid symbol labels."""
        validator = LabelValidator()
        assert validator.sanitize_label_value("symbol", "BTCUSDT") == "BTCUSDT"
        assert validator.sanitize_label_value("symbol", "ETHUSDT") == "ETHUSDT"

    def test_sanitize_invalid_symbol(self) -> None:
        """Test sanitizing invalid symbol labels."""
        validator = LabelValidator()
        # Unknown symbols should be replaced with "unknown"
        result = validator.sanitize_label_value("symbol", "INVALID")
        assert result == "unknown"

    def test_sanitize_timeframe(self) -> None:
        """Test sanitizing timeframe labels."""
        validator = LabelValidator()
        assert validator.sanitize_label_value("timeframe", "1m") == "1m"
        assert validator.sanitize_label_value("timeframe", "1h") == "1h"

    def test_sanitize_invalid_timeframe(self) -> None:
        """Test sanitizing invalid timeframe."""
        validator = LabelValidator()
        result = validator.sanitize_label_value("timeframe", "30s")
        assert result == "unknown"

    def test_sanitize_stream(self) -> None:
        """Test sanitizing stream labels."""
        validator = LabelValidator()
        assert validator.sanitize_label_value("stream", "klines") == "klines"
        assert validator.sanitize_label_value("stream", "invalid") == "unknown"

    def test_sanitize_long_value(self) -> None:
        """Test that long values are truncated."""
        validator = LabelValidator()
        long_value = "a" * 100
        result = validator.sanitize_label_value("test", long_value)
        assert len(result) <= 64

    def test_sanitize_invalid_chars(self) -> None:
        """Test that invalid characters are replaced."""
        validator = LabelValidator()
        result = validator.sanitize_label_value("test", "hello@world!")
        assert "@" not in result
        assert "!" not in result

    def test_cardinality_limit(self) -> None:
        """Test cardinality limit enforcement."""
        validator = LabelValidator()

        # Add values up to the limit
        for i in range(MAX_CARDINALITY + 5):
            allowed = validator.check_cardinality("test_metric", (f"value_{i}",))
            if i < MAX_CARDINALITY:
                assert allowed is True
            else:
                assert allowed is False


class TestMetricKey:
    """Test suite for MetricKey."""

    def test_from_labels(self) -> None:
        """Test creating MetricKey from labels dict."""
        key = MetricKey.from_labels({"symbol": "BTCUSDT", "stream": "klines"})
        assert isinstance(key.labels, tuple)
        assert ("symbol", "BTCUSDT") in key.labels
        assert ("stream", "klines") in key.labels

    def test_render_empty(self) -> None:
        """Test rendering empty labels."""
        key = MetricKey.from_labels({})
        assert key.render() == ""

    def test_render_with_labels(self) -> None:
        """Test rendering with labels."""
        key = MetricKey.from_labels({"symbol": "BTCUSDT"})
        rendered = key.render()
        assert 'symbol="BTCUSDT"' in rendered
        assert rendered.startswith("{")
        assert rendered.endswith("}")


class TestCounter:
    """Test suite for Counter metric."""

    def test_counter_creation(self) -> None:
        """Test counter creation."""
        counter = Counter(
            "test_counter",
            "Test counter",
            ("label1",),
        )
        assert counter.name == "test_counter"
        assert counter.help_text == "Test counter"
        assert counter.label_names == ("label1",)

    def test_counter_increment(self) -> None:
        """Test counter increment."""
        counter = Counter("test", "Test", ("symbol",))
        counter.inc(labels={"symbol": "BTCUSDT"})
        key = MetricKey.from_labels({"symbol": "BTCUSDT"})
        assert counter.values[key] == 1

    def test_counter_increment_multiple(self) -> None:
        """Test multiple increments."""
        counter = Counter("test", "Test", ("symbol",))
        counter.inc(amount=5, labels={"symbol": "BTCUSDT"})
        key = MetricKey.from_labels({"symbol": "BTCUSDT"})
        assert counter.values[key] == 5

    def test_counter_with_labels(self) -> None:
        """Test counter with_labels method."""
        counter = Counter("test", "Test", ("symbol",))
        counter.with_labels(symbol="BTCUSDT")
        key = MetricKey.from_labels({"symbol": "BTCUSDT"})
        assert key in counter.values

    def test_counter_wrong_labels(self) -> None:
        """Test counter with wrong labels raises error."""
        counter = Counter("test", "Test", ("symbol",))
        with pytest.raises(ValueError, match="Counter labels do not match"):
            counter.with_labels(wrong_label="BTCUSDT")

    def test_counter_cardinality_limit(self) -> None:
        """Test counter respects cardinality limit."""
        counter = Counter("test", "Test", ("id",))

        # Add values up to limit
        for i in range(MAX_CARDINALITY + 5):
            counter.inc(labels={"id": f"value_{i}"})

        # Should have exactly MAX_CARDINALITY values
        assert len(counter.values) <= MAX_CARDINALITY


class TestGauge:
    """Test suite for Gauge metric."""

    def test_gauge_creation(self) -> None:
        """Test gauge creation."""
        gauge = Gauge(
            "test_gauge",
            "Test gauge",
            ("label1",),
        )
        assert gauge.name == "test_gauge"
        assert gauge.help_text == "Test gauge"

    def test_gauge_set(self) -> None:
        """Test gauge set."""
        gauge = Gauge("test", "Test", ("symbol",))
        gauge.set(42.0, labels={"symbol": "BTCUSDT"})
        key = MetricKey.from_labels({"symbol": "BTCUSDT"})
        assert gauge.values[key] == 42.0

    def test_gauge_update(self) -> None:
        """Test gauge value update."""
        gauge = Gauge("test", "Test", ("symbol",))
        gauge.set(42.0, labels={"symbol": "BTCUSDT"})
        gauge.set(100.0, labels={"symbol": "BTCUSDT"})
        key = MetricKey.from_labels({"symbol": "BTCUSDT"})
        assert gauge.values[key] == 100.0

    def test_gauge_with_labels(self) -> None:
        """Test gauge with_labels method."""
        gauge = Gauge("test", "Test", ("symbol",))
        gauge.with_labels(symbol="BTCUSDT")
        key = MetricKey.from_labels({"symbol": "BTCUSDT"})
        assert key in gauge.values


class TestMetricsRegistry:
    """Test suite for MetricsRegistry."""

    def test_registry_render(self) -> None:
        """Test registry render output."""
        registry = MetricsRegistry()
        counter = Counter("test_counter", "Test counter", ())
        counter.inc()
        registry.counters.append(counter)

        output = registry.render()
        assert "# HELP test_counter Test counter" in output
        assert "# TYPE test_counter counter" in output
        assert "test_counter 1" in output

    def test_registry_cardinality_stats(self) -> None:
        """Test getting cardinality stats."""
        registry = MetricsRegistry()
        counter = Counter("test_counter", "Test", ("symbol",))
        counter.inc(labels={"symbol": "BTCUSDT"})
        counter.inc(labels={"symbol": "ETHUSDT"})
        registry.counters.append(counter)

        stats = registry.get_cardinality_stats()
        assert stats["test_counter"] == 2

    def test_registry_multiple_metrics(self) -> None:
        """Test registry with multiple metrics."""
        registry = MetricsRegistry()

        counter = Counter("messages", "Message count", ("symbol",))
        counter.inc(labels={"symbol": "BTCUSDT"})
        registry.counters.append(counter)

        gauge = Gauge("latency", "Latency", ())
        gauge.set(0.5)
        registry.gauges.append(gauge)

        output = registry.render()
        assert "messages" in output
        assert "latency" in output
