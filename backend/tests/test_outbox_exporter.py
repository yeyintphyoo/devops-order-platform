"""Unit tests for the Prometheus transactional outbox exporter."""

import sys
import importlib
from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry


@pytest.fixture
def exporter(monkeypatch):
    """Load exporter with a mocked engine and isolated metrics."""
    import prometheus_client
    import sqlalchemy

    mock_engine = MagicMock()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://test:test@localhost:5432/test_db",
    )

    monkeypatch.setattr(
        sqlalchemy,
        "create_engine",
        lambda *args, **kwargs: mock_engine,
    )

    registry = CollectorRegistry()
    original_gauge = prometheus_client.Gauge

    def isolated_gauge(*args, **kwargs):
        kwargs["registry"] = registry
        return original_gauge(*args, **kwargs)

    monkeypatch.setattr(
        prometheus_client,
        "Gauge",
        isolated_gauge,
    )

    # Reload exactly once per fixture invocation.
    module_name = "backend.app.outbox_exporter"

    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    return module, mock_engine


def test_collect_outbox_metrics_success(exporter, monkeypatch):
    module, mock_engine = exporter

    result = {
        "total_events": 10,
        "published_events": 7,
        "pending_events": 3,
        "retry_pending_events": 1,
        "active_leases": 1,
        "expired_leases": 0,
        "retry_due_events": 1,
        "max_retry_count": 2,
        "oldest_pending_age_seconds": 45,
    }

    connection = mock_engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.one.return_value = result

    monkeypatch.setattr(module.time, "time", lambda: 1234567890.0)

    module.collect_outbox_metrics()

    for column, gauge in module.METRICS.items():
        assert gauge._value.get() == float(result[column])

    assert module.DB_UP._value.get() == 1
    assert module.LAST_SUCCESS._value.get() == 1234567890.0


def test_collect_outbox_metrics_database_failure(exporter):
    module, mock_engine = exporter

    mock_engine.connect.side_effect = RuntimeError("PostgreSQL unavailable")

    module.collect_outbox_metrics()

    assert module.DB_UP._value.get() == 0


def test_collect_outbox_metrics_preserves_values_on_failure(exporter):
    module, mock_engine = exporter

    for gauge in module.METRICS.values():
        gauge.set(99)

    module.LAST_SUCCESS.set(12345)

    mock_engine.connect.side_effect = RuntimeError("Database unavailable")

    module.collect_outbox_metrics()

    for gauge in module.METRICS.values():
        assert gauge._value.get() == 99

    assert module.LAST_SUCCESS._value.get() == 12345
    assert module.DB_UP._value.get() == 0


def test_main_rejects_invalid_poll_interval(exporter, monkeypatch):
    module, _ = exporter

    monkeypatch.setattr(module, "POLL_INTERVAL", 0)

    with pytest.raises(ValueError, match="must be positive"):
        module.main()


def test_main_starts_http_server(exporter, monkeypatch):
    module, _ = exporter

    mock_collect = MagicMock()
    mock_server = MagicMock()

    monkeypatch.setattr(
        module,
        "collect_outbox_metrics",
        mock_collect,
    )

    monkeypatch.setattr(
        module,
        "start_http_server",
        mock_server,
    )

    def stop_loop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(module.time, "sleep", stop_loop)

    with pytest.raises(KeyboardInterrupt):
        module.main()

    mock_collect.assert_called_once()

    mock_server.assert_called_once_with(
        module.EXPORTER_PORT,
        addr="0.0.0.0",
    )


def test_exporter_query_contains_required_metrics(exporter):
    module, _ = exporter

    query = str(module.QUERY)

    assert "FROM outbox_events" in query
    assert "pending_events" in query
    assert "published_events" in query
    assert "expired_leases" in query
    assert "oldest_pending_age_seconds" in query
