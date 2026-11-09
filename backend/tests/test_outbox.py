"""Unit tests for transactional outbox database operations."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from backend.app.outbox import (
    claim_outbox_events,
    create_publisher_id,
    mark_event_published,
    record_publish_failure,
)


@pytest.fixture
def db():
    return MagicMock()


def make_event():
    event = MagicMock()
    event.event_id = "test-event-123"
    event.event_type = "order.created"
    event.retry_count = 0
    event.published_at = None
    event.locked_by = None
    event.locked_until = None
    event.next_attempt_at = None
    event.last_error = None
    return event


def test_claim_outbox_events_success(db):
    event = make_event()
    db.scalars.return_value.all.return_value = [event]

    events = claim_outbox_events(
        db=db,
        publisher_id="publisher-1",
        batch_size=5,
        lock_seconds=60,
    )

    assert events == [event]
    assert event.locked_by == "publisher-1"
    assert event.locked_until > datetime.now(timezone.utc)

    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_claim_outbox_events_empty(db):
    db.scalars.return_value.all.return_value = []

    events = claim_outbox_events(
        db=db,
        publisher_id="publisher-1",
    )

    assert events == []
    db.commit.assert_called_once()


@pytest.mark.parametrize(
    "batch_size",
    [0, -1],
)
def test_claim_rejects_invalid_batch_size(db, batch_size):
    with pytest.raises(ValueError, match="batch_size"):
        claim_outbox_events(
            db=db,
            publisher_id="publisher-1",
            batch_size=batch_size,
        )

    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "lock_seconds",
    [0, -1],
)
def test_claim_rejects_invalid_lock_seconds(db, lock_seconds):
    with pytest.raises(ValueError, match="lock_seconds"):
        claim_outbox_events(
            db=db,
            publisher_id="publisher-1",
            lock_seconds=lock_seconds,
        )

    db.commit.assert_not_called()


def test_claim_rolls_back_on_database_failure(db):
    db.scalars.side_effect = RuntimeError("Database unavailable")

    with pytest.raises(RuntimeError, match="Database unavailable"):
        claim_outbox_events(
            db=db,
            publisher_id="publisher-1",
        )

    db.rollback.assert_called_once()


def test_record_publish_failure_success(db):
    event = make_event()
    db.scalar.return_value = event

    result = record_publish_failure(
        db=db,
        event_id=event.event_id,
        publisher_id="publisher-1",
        error="RabbitMQ unavailable",
        base_retry_seconds=5,
        max_retry_seconds=300,
    )

    assert result is True
    assert event.retry_count == 1
    assert event.last_error == "RabbitMQ unavailable"
    assert event.next_attempt_at is not None
    assert event.locked_by is None
    assert event.locked_until is None

    db.commit.assert_called_once()


def test_record_publish_failure_exponential_backoff(db):
    event = make_event()
    event.retry_count = 2
    db.scalar.return_value = event

    before = datetime.now(timezone.utc)

    result = record_publish_failure(
        db=db,
        event_id=event.event_id,
        publisher_id="publisher-1",
        error="Temporary failure",
        base_retry_seconds=5,
        max_retry_seconds=300,
    )

    after = datetime.now(timezone.utc)

    assert result is True
    assert event.retry_count == 3

    expected_delay = timedelta(seconds=20)

    assert before + expected_delay <= event.next_attempt_at
    assert event.next_attempt_at <= after + expected_delay


def test_record_publish_failure_retry_delay_is_capped(db):
    event = make_event()
    event.retry_count = 10
    db.scalar.return_value = event

    before = datetime.now(timezone.utc)

    result = record_publish_failure(
        db=db,
        event_id=event.event_id,
        publisher_id="publisher-1",
        error="Still unavailable",
        base_retry_seconds=5,
        max_retry_seconds=30,
    )

    after = datetime.now(timezone.utc)

    assert result is True
    assert event.retry_count == 11
    assert before + timedelta(seconds=30) <= event.next_attempt_at
    assert event.next_attempt_at <= after + timedelta(seconds=30)


def test_record_publish_failure_truncates_error(db):
    event = make_event()
    db.scalar.return_value = event

    result = record_publish_failure(
        db=db,
        event_id=event.event_id,
        publisher_id="publisher-1",
        error="x" * 3000,
    )

    assert result is True
    assert len(event.last_error) == 2000


def test_record_publish_failure_when_lease_is_lost(db):
    db.scalar.return_value = None

    result = record_publish_failure(
        db=db,
        event_id="missing-event",
        publisher_id="publisher-1",
        error="Failure",
    )

    assert result is False
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "base_retry_seconds,max_retry_seconds",
    [
        (0, 300),
        (-1, 300),
        (10, 5),
    ],
)
def test_record_publish_failure_rejects_invalid_retry_config(
    db,
    base_retry_seconds,
    max_retry_seconds,
):
    with pytest.raises(ValueError):
        record_publish_failure(
            db=db,
            event_id="event-1",
            publisher_id="publisher-1",
            error="Failure",
            base_retry_seconds=base_retry_seconds,
            max_retry_seconds=max_retry_seconds,
        )

    db.commit.assert_not_called()


def test_record_publish_failure_rolls_back(db):
    db.scalar.side_effect = RuntimeError("Database failure")

    with pytest.raises(RuntimeError):
        record_publish_failure(
            db=db,
            event_id="event-1",
            publisher_id="publisher-1",
            error="Failure",
        )

    db.rollback.assert_called_once()


def test_mark_event_published_success(db):
    event = make_event()
    event.locked_by = "publisher-1"
    event.locked_until = datetime.now(timezone.utc) + timedelta(seconds=60)
    event.next_attempt_at = datetime.now(timezone.utc)
    event.last_error = "Previous failure"

    db.scalar.return_value = event

    result = mark_event_published(
        db=db,
        event_id=event.event_id,
        publisher_id="publisher-1",
    )

    assert result is True
    assert event.published_at is not None
    assert event.locked_by is None
    assert event.locked_until is None
    assert event.next_attempt_at is None
    assert event.last_error is None

    db.commit.assert_called_once()


def test_mark_event_published_when_lease_is_lost(db):
    db.scalar.return_value = None

    result = mark_event_published(
        db=db,
        event_id="missing-event",
        publisher_id="publisher-1",
    )

    assert result is False
    db.commit.assert_not_called()


def test_mark_event_published_rolls_back(db):
    db.scalar.side_effect = RuntimeError("Database failure")

    with pytest.raises(RuntimeError):
        mark_event_published(
            db=db,
            event_id="event-1",
            publisher_id="publisher-1",
        )

    db.rollback.assert_called_once()


def test_create_publisher_id_returns_uuid():
    publisher_id = create_publisher_id()

    assert isinstance(publisher_id, str)
    assert str(UUID(publisher_id)) == publisher_id


def test_create_publisher_id_is_unique():
    first = create_publisher_id()
    second = create_publisher_id()

    assert first != second
