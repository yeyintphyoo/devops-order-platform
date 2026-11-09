"""Unit tests for idempotent order event processing."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.consumer import process_order_created_event


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def event():
    return SimpleNamespace(
        event_id=uuid4(),
        event_type="order.created",
    )


def test_process_order_created_event_success(db, event):
    db.scalar.return_value = None

    result = process_order_created_event(db, event)

    assert result is True

    db.add.assert_called_once()
    db.commit.assert_called_once()
    db.rollback.assert_not_called()

    processed = db.add.call_args.args[0]

    assert processed.event_id == str(event.event_id)
    assert processed.event_type == event.event_type


def test_process_order_created_event_duplicate(db, event):
    db.scalar.return_value = MagicMock()

    result = process_order_created_event(db, event)

    assert result is False

    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_process_order_created_event_integrity_error_duplicate(db, event):
    db.scalar.side_effect = [
        None,
        MagicMock(),
    ]

    db.commit.side_effect = IntegrityError(
        statement="INSERT",
        params={},
        orig=Exception("duplicate key"),
    )

    result = process_order_created_event(db, event)

    assert result is False
    db.rollback.assert_called_once()
    assert db.scalar.call_count == 2


def test_process_order_created_event_integrity_error_without_duplicate(
    db,
    event,
):
    db.scalar.side_effect = [
        None,
        None,
    ]

    db.commit.side_effect = IntegrityError(
        statement="INSERT",
        params={},
        orig=Exception("integrity failure"),
    )

    with pytest.raises(IntegrityError):
        process_order_created_event(db, event)

    db.rollback.assert_called_once()


def test_process_order_created_event_database_failure(db, event):
    db.scalar.return_value = None
    db.commit.side_effect = RuntimeError("Database unavailable")

    with pytest.raises(RuntimeError, match="Database unavailable"):
        process_order_created_event(db, event)

    db.rollback.assert_called_once()
