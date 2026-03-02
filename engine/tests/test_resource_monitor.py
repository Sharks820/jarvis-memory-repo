"""Tests for ResourceMonitor — usage caps and z-score anomaly detection."""

from __future__ import annotations

import threading

import pytest

from jarvis_engine.security.resource_monitor import ResourceMonitor


class TestRecordAndCheck:
    """record() stores values and check_cap() reports within bounds."""

    def test_record_and_check(self):
        mon = ResourceMonitor()
        mon.record("tokens_per_day", 1000)
        within, current = mon.check_cap("tokens_per_day")
        assert within is True
        assert current == 1000

    def test_record_accumulates(self):
        mon = ResourceMonitor()
        mon.record("tokens_per_day", 1000)
        mon.record("tokens_per_day", 2000)
        within, current = mon.check_cap("tokens_per_day")
        assert within is True
        assert current == 3000


class TestCapExceeded:
    """Cap exceeded flag set when cumulative value exceeds the hard cap."""

    def test_cap_exceeded(self):
        mon = ResourceMonitor(caps={"tokens_per_day": 100})
        mon.record("tokens_per_day", 50)
        within, _ = mon.check_cap("tokens_per_day")
        assert within is True

        mon.record("tokens_per_day", 60)  # 110 > 100
        within, current = mon.check_cap("tokens_per_day")
        assert within is False
        assert current == 110

    def test_cap_exceeded_in_status(self):
        mon = ResourceMonitor(caps={"api_calls_per_hour": 5})
        for _ in range(6):
            mon.record("api_calls_per_hour", 1)
        st = mon.status()
        assert "api_calls_per_hour" in st["cap_exceeded"]


class TestAnomalyDetection:
    """Z-score anomaly detection triggers on extreme outliers."""

    def test_anomaly_detection(self):
        mon = ResourceMonitor(z_threshold=2.0, window_size=50)
        # Populate with normal values
        for _ in range(20):
            mon.record("latency", 100.0)
        # Extreme outlier
        mon.record("latency", 10000.0)
        assert mon.is_anomalous("latency") is True

    def test_no_anomaly_for_normal_values(self):
        mon = ResourceMonitor(z_threshold=3.0, window_size=50)
        for i in range(20):
            mon.record("latency", 100.0 + i)
        assert mon.is_anomalous("latency") is False


class TestStatusReport:
    """status() returns expected structure."""

    def test_status_report(self):
        mon = ResourceMonitor()
        mon.record("tokens_per_day", 5000)
        mon.record("api_calls_per_hour", 10)
        st = mon.status()

        assert "metrics" in st
        assert "anomalies" in st
        assert "cap_exceeded" in st

        tok = st["metrics"]["tokens_per_day"]
        assert tok["current"] == 5000
        assert tok["cap"] == 500000
        assert isinstance(tok["utilization_pct"], float)
        assert tok["anomalous"] is False
        assert tok["values_count"] == 1

    def test_utilization_pct_calculation(self):
        mon = ResourceMonitor(caps={"x": 200})
        mon.record("x", 50)
        st = mon.status()
        assert st["metrics"]["x"]["utilization_pct"] == pytest.approx(25.0)


class TestResetDaily:
    """reset_daily() clears cumulative counters."""

    def test_reset_daily(self):
        mon = ResourceMonitor()
        mon.record("tokens_per_day", 100000)
        _, current = mon.check_cap("tokens_per_day")
        assert current == 100000

        mon.reset_daily()
        _, current = mon.check_cap("tokens_per_day")
        assert current == 0.0

    def test_reset_clears_cap_exceeded(self):
        mon = ResourceMonitor(caps={"tokens_per_day": 100})
        mon.record("tokens_per_day", 200)
        within, _ = mon.check_cap("tokens_per_day")
        assert within is False

        mon.reset_daily()
        within, current = mon.check_cap("tokens_per_day")
        assert within is True
        assert current == 0.0


class TestZScoreNeedsMinData:
    """No anomaly flagged with fewer than 10 data points."""

    def test_z_score_needs_min_data(self):
        mon = ResourceMonitor(z_threshold=2.0)
        # Only 5 values — should not flag anomaly even with outlier
        for _ in range(4):
            mon.record("cpu", 50.0)
        mon.record("cpu", 50000.0)  # Extreme outlier but < 10 points
        assert mon.is_anomalous("cpu") is False

    def test_anomaly_at_exactly_10(self):
        """Once we hit 10 values, anomaly detection engages."""
        mon = ResourceMonitor(z_threshold=2.0)
        for _ in range(9):
            mon.record("cpu", 50.0)
        mon.record("cpu", 50000.0)  # 10th value is extreme
        assert mon.is_anomalous("cpu") is True


class TestCustomCaps:
    """Custom caps override defaults."""

    def test_custom_caps(self):
        custom = {"my_metric": 42}
        mon = ResourceMonitor(caps=custom)
        mon.record("my_metric", 40)
        within, _ = mon.check_cap("my_metric")
        assert within is True

        mon.record("my_metric", 5)  # 45 > 42
        within, current = mon.check_cap("my_metric")
        assert within is False
        assert current == 45

    def test_default_caps_absent_for_custom(self):
        """Custom caps replace defaults entirely."""
        custom = {"my_metric": 10}
        mon = ResourceMonitor(caps=custom)
        # Default 'tokens_per_day' cap should not exist
        within, current = mon.check_cap("tokens_per_day")
        # No cap -> always within
        assert within is True
        assert current == 0.0


class TestThreadSafety:
    """Concurrent record() calls must not corrupt state."""

    def test_concurrent_records(self):
        mon = ResourceMonitor(caps={"counter": 1_000_000})
        n_threads = 10
        n_per_thread = 100

        def worker():
            for _ in range(n_per_thread):
                mon.record("counter", 1)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        _, current = mon.check_cap("counter")
        assert current == n_threads * n_per_thread


class TestUnknownMetric:
    """Querying a metric that was never recorded returns safe defaults."""

    def test_check_cap_unknown(self):
        mon = ResourceMonitor()
        within, current = mon.check_cap("nonexistent")
        assert within is True
        assert current == 0.0

    def test_is_anomalous_unknown(self):
        mon = ResourceMonitor()
        assert mon.is_anomalous("nonexistent") is False
