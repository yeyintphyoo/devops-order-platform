"""Unit tests for the transactional outbox publisher."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app import publisher


@pytest.fixture
def db():
    return MagicMock()


def make_event(event_id="event-123"):
    return SimpleNamespace(
        event_id=event_id,
        event_type="order.created",
        payload={"event_type": "order.created", "order_id": 123},
    )


def test_process_outbox_batch_empty(db, monkeypatch):
    mock_claim = MagicMock(return_value=[])
    mock_publish = MagicMock()

    monkeypatch.setattr(publisher, "claim_outbox_events", mock_claim)
    monkeypatch.setattr(publisher, "publish_order_event", mock_publish)

    result = publisher.process_outbox_batch(db, "publisher-1")

    assert result == 0
    mock_publish.assert_not_called()
    mock_claim.assert_called_once_with(
        db=db,
        publisher_id="publisher-1",
        batch_size=publisher.BATCH_SIZE,
        lock_seconds=publisher.LOCK_SECONDS,
    )


def test_process_outbox_batch_success(db, monkeypatch):
    event = make_event()

    mock_claim = MagicMock(return_value=[event])
    mock_publish = MagicMock()
    mock_mark = MagicMock(return_value=True)

    monkeypatch.setattr(publisher, "claim_outbox_events", mock_claim)
    monkeypatch.setattr(publisher, "publish_order_event", mock_publish)
    monkeypatch.setattr(publisher, "mark_event_published", mock_mark)

    result = publisher.process_outbox_batch(db, "publisher-1")

    assert result == 1

    mock_publish.assert_called_once_with(
        event=event.payload,
        routing_key=event.event_type,
    )

    mock_mark.assert_called_once_with(
        db=db,
        event_id=event.event_id,
        publisher_id="publisher-1",
    )


def test_process_outbox_batch_lost_lease(db, monkeypatch):
    event = make_event()

    monkeypatch.setattr(
        publisher,
        "claim_outbox_events",
        MagicMock(return_value=[event]),
    )

    monkeypatch.setattr(
        publisher,
        "publish_order_event",
        MagicMock(),
    )

    monkeypatch.setattr(
        publisher,
        "mark_event_published",
        MagicMock(return_value=False),
    )

    result = publisher.process_outbox_batch(db, "publisher-1")

    assert result == 0


def test_process_outbox_batch_publish_failure(db, monkeypatch):
    event = make_event()

    mock_record = MagicMock(return_value=True)

    monkeypatch.setattr(
        publisher,
        "claim_outbox_events",
        MagicMock(return_value=[event]),
    )

    monkeypatch.setattr(
        publisher,
        "publish_order_event",
        MagicMock(side_effect=RuntimeError("RabbitMQ unavailable")),
    )

    monkeypatch.setattr(
        publisher,
        "record_publish_failure",
        mock_record,
    )

    result = publisher.process_outbox_batch(db, "publisher-1")

    assert result == 0

    mock_record.assert_called_once_with(
        db=db,
        event_id=event.event_id,
        publisher_id="publisher-1",
        error="RabbitMQ unavailable",
        max_retry_seconds=publisher.MAX_RETRY_SECONDS,
    )


def test_process_outbox_batch_failure_record_not_owned(db, monkeypatch):
    event = make_event()

    monkeypatch.setattr(
        publisher,
        "claim_outbox_events",
        MagicMock(return_value=[event]),
    )

    monkeypatch.setattr(
        publisher,
        "publish_order_event",
        MagicMock(side_effect=RuntimeError("Publish failed")),
    )

    mock_record = MagicMock(return_value=False)

    monkeypatch.setattr(
        publisher,
        "record_publish_failure",
        mock_record,
    )

    result = publisher.process_outbox_batch(db, "publisher-1")

    assert result == 0
    mock_record.assert_called_once()


def test_process_outbox_batch_failure_record_raises(db, monkeypatch):
    event = make_event()

    monkeypatch.setattr(
        publisher,
        "claim_outbox_events",
        MagicMock(return_value=[event]),
    )

    monkeypatch.setattr(
        publisher,
        "publish_order_event",
        MagicMock(side_effect=RuntimeError("Publish failed")),
    )

    monkeypatch.setattr(
        publisher,
        "record_publish_failure",
        MagicMock(side_effect=RuntimeError("Database unavailable")),
    )

    result = publisher.process_outbox_batch(db, "publisher-1")

    assert result == 0


def test_process_outbox_batch_multiple_events(db, monkeypatch):
    events = [
        make_event("event-1"),
        make_event("event-2"),
        make_event("event-3"),
    ]

    mock_publish = MagicMock()
    mock_mark = MagicMock(return_value=True)

    monkeypatch.setattr(
        publisher,
        "claim_outbox_events",
        MagicMock(return_value=events),
    )

    monkeypatch.setattr(publisher, "publish_order_event", mock_publish)
    monkeypatch.setattr(publisher, "mark_event_published", mock_mark)

    result = publisher.process_outbox_batch(db, "publisher-1")

    assert result == 3
    assert mock_publish.call_count == 3
    assert mock_mark.call_count == 3


def test_run_publisher_processes_batch_and_closes_session(monkeypatch):
    db = MagicMock()
    stop_event = MagicMock()

    stop_event.is_set.side_effect = [False, True]

    mock_process = MagicMock()

    monkeypatch.setattr(
        publisher,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    monkeypatch.setattr(
        publisher,
        "create_publisher_id",
        MagicMock(return_value="publisher-1"),
    )

    monkeypatch.setattr(
        publisher,
        "process_outbox_batch",
        mock_process,
    )

    publisher.run_publisher(stop_event)

    mock_process.assert_called_once_with(
        db=db,
        publisher_id="publisher-1",
    )

    db.close.assert_called_once()
    stop_event.wait.assert_called_once_with(publisher.POLL_INTERVAL_SECONDS)


def test_run_publisher_closes_session_after_failure(monkeypatch):
    db = MagicMock()
    stop_event = MagicMock()

    stop_event.is_set.side_effect = [False, True]

    monkeypatch.setattr(
        publisher,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    monkeypatch.setattr(
        publisher,
        "create_publisher_id",
        MagicMock(return_value="publisher-1"),
    )

    monkeypatch.setattr(
        publisher,
        "process_outbox_batch",
        MagicMock(side_effect=RuntimeError("Batch failed")),
    )

    publisher.run_publisher(stop_event)

    db.close.assert_called_once()
    stop_event.wait.assert_called_once()


def test_run_publisher_stops_immediately(monkeypatch):
    stop_event = MagicMock()
    stop_event.is_set.return_value = True

    mock_session = MagicMock()

    monkeypatch.setattr(publisher, "SessionLocal", mock_session)

    publisher.run_publisher(stop_event)

    mock_session.assert_not_called()
    stop_event.wait.assert_not_called()


def test_publisher_main_configures_signals_and_shutdown(monkeypatch):
    """Verify publisher startup and graceful shutdown without real services."""

    import signal
    from unittest.mock import MagicMock

    mock_run = MagicMock()
    registered_handlers = {}

    def fake_signal(signum, handler):
        registered_handlers[signum] = handler

    def fake_run_publisher(stop_event):
        mock_run(stop_event)

        # Simulate receiving SIGTERM.
        registered_handlers[signal.SIGTERM](
            signal.SIGTERM,
            None,
        )

        assert stop_event.is_set()

    monkeypatch.setattr(
        publisher.signal,
        "signal",
        fake_signal,
    )

    monkeypatch.setattr(
        publisher,
        "run_publisher",
        fake_run_publisher,
    )

    publisher.main()

    assert signal.SIGTERM in registered_handlers
    assert signal.SIGINT in registered_handlers

    mock_run.assert_called_once()
