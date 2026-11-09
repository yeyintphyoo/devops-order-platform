from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import ProcessedEvent
from .schemas import OrderCreatedEvent


def process_order_created_event(
    db: Session,
    event: OrderCreatedEvent,
) -> bool:
    """
    Record an order.created event exactly once.

    Returns:
        True: event was newly processed.
        False: event was already processed.
    """

    existing_event = db.scalar(
        select(ProcessedEvent).where(ProcessedEvent.event_id == str(event.event_id))
    )

    if existing_event:
        return False

    try:
        processed_event = ProcessedEvent(
            event_id=str(event.event_id),
            event_type=event.event_type,
        )

        db.add(processed_event)
        db.commit()

        return True

    except IntegrityError:
        db.rollback()

        # Another consumer may have inserted the same event
        # concurrently. The unique constraint protects against duplicates.
        existing_event = db.scalar(
            select(ProcessedEvent).where(ProcessedEvent.event_id == str(event.event_id))
        )

        if existing_event:
            return False

        raise

    except Exception:
        db.rollback()
        raise
