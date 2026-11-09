"""Database operations for the transactional outbox."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import OutboxEvent


def claim_outbox_events(
    db: Session,
    publisher_id: str,
    batch_size: int = 10,
    lock_seconds: int = 60,
) -> list[OutboxEvent]:
    """Claim eligible unpublished events using a time-limited lease."""
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")

    if lock_seconds < 1:
        raise ValueError("lock_seconds must be greater than zero")

    now = datetime.now(timezone.utc)
    locked_until = now + timedelta(seconds=lock_seconds)

    statement = (
        select(OutboxEvent)
        .where(
            OutboxEvent.published_at.is_(None),
            or_(
                OutboxEvent.next_attempt_at.is_(None),
                OutboxEvent.next_attempt_at <= now,
            ),
            or_(
                OutboxEvent.locked_until.is_(None),
                OutboxEvent.locked_until <= now,
            ),
        )
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    try:
        events = list(db.scalars(statement).all())

        for event in events:
            event.locked_by = publisher_id
            event.locked_until = locked_until

        db.commit()

        return events

    except Exception:
        db.rollback()
        raise


def record_publish_failure(
    db: Session,
    event_id: str,
    publisher_id: str,
    error: str,
    base_retry_seconds: int = 5,
    max_retry_seconds: int = 300,
) -> bool:
    """Record a failed publication and schedule its next attempt."""
    if base_retry_seconds < 1:
        raise ValueError("base_retry_seconds must be greater than zero")

    if max_retry_seconds < base_retry_seconds:
        raise ValueError(
            "max_retry_seconds must be greater than or equal to base_retry_seconds"
        )

    try:
        now = datetime.now(timezone.utc)

        event = db.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.event_id == event_id,
                OutboxEvent.locked_by == publisher_id,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.locked_until > now,
            )
            .with_for_update()
        )

        if event is None:
            return False

        event.retry_count += 1

        retry_delay = min(
            base_retry_seconds * (2 ** (event.retry_count - 1)),
            max_retry_seconds,
        )

        event.last_error = error[:2000]
        event.next_attempt_at = now + timedelta(seconds=retry_delay)
        event.locked_by = None
        event.locked_until = None

        db.commit()

        return True

    except Exception:
        db.rollback()
        raise


def mark_event_published(
    db: Session,
    event_id: str,
    publisher_id: str,
) -> bool:
    """Mark an event as published after broker confirmation."""
    try:
        now = datetime.now(timezone.utc)

        event = db.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.event_id == event_id,
                OutboxEvent.locked_by == publisher_id,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.locked_until > now,
            )
            .with_for_update()
        )

        if event is None:
            return False

        event.published_at = now
        event.locked_by = None
        event.locked_until = None
        event.next_attempt_at = None
        event.last_error = None

        db.commit()

        return True

    except Exception:
        db.rollback()
        raise


def create_publisher_id() -> str:
    """Generate a unique identifier for a publisher process."""
    return str(uuid4())
