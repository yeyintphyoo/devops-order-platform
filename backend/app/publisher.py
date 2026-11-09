"""Background worker that publishes transactional outbox events."""

import logging
import os
import signal
import threading

from sqlalchemy.orm import Session

from .database import SessionLocal
from .messaging import publish_order_event
from .outbox import (
    claim_outbox_events,
    create_publisher_id,
    mark_event_published,
    record_publish_failure,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = float(os.getenv("OUTBOX_POLL_INTERVAL_SECONDS", "5"))
BATCH_SIZE = int(os.getenv("OUTBOX_BATCH_SIZE", "5"))
LOCK_SECONDS = int(os.getenv("OUTBOX_LOCK_SECONDS", "120"))
MAX_RETRY_SECONDS = int(os.getenv("OUTBOX_MAX_RETRY_SECONDS", "300"))


if POLL_INTERVAL_SECONDS <= 0:
    raise ValueError("OUTBOX_POLL_INTERVAL_SECONDS must be greater than zero")

if BATCH_SIZE < 1:
    raise ValueError("OUTBOX_BATCH_SIZE must be greater than zero")

if LOCK_SECONDS < 1:
    raise ValueError("OUTBOX_LOCK_SECONDS must be greater than zero")

if MAX_RETRY_SECONDS < 5:
    raise ValueError("OUTBOX_MAX_RETRY_SECONDS must be at least 5")


def process_outbox_batch(
    db: Session,
    publisher_id: str,
) -> int:
    """Claim and process one batch of pending outbox events."""
    events = claim_outbox_events(
        db=db,
        publisher_id=publisher_id,
        batch_size=BATCH_SIZE,
        lock_seconds=LOCK_SECONDS,
    )

    if not events:
        return 0

    logger.info(
        "Claimed %s outbox event(s)",
        len(events),
    )

    processed = 0

    for event in events:
        try:
            publish_order_event(
                event=event.payload,
                routing_key=event.event_type,
            )

            marked = mark_event_published(
                db=db,
                event_id=event.event_id,
                publisher_id=publisher_id,
            )

            if marked:
                logger.info(
                    "Outbox event published event_id=%s event_type=%s",
                    event.event_id,
                    event.event_type,
                )
                processed += 1
            else:
                logger.warning(
                    "Could not mark event as published; "
                    "publisher no longer owns its lease event_id=%s",
                    event.event_id,
                )

        except Exception as exc:
            logger.exception(
                "Failed to process outbox event event_id=%s",
                event.event_id,
            )

            try:
                recorded = record_publish_failure(
                    db=db,
                    event_id=event.event_id,
                    publisher_id=publisher_id,
                    error=str(exc),
                    max_retry_seconds=MAX_RETRY_SECONDS,
                )

                if not recorded:
                    logger.warning(
                        "Could not record failure; "
                        "publisher no longer owns its lease event_id=%s",
                        event.event_id,
                    )

            except Exception:
                logger.exception(
                    "Could not record outbox failure event_id=%s",
                    event.event_id,
                )

    return processed


def run_publisher(stop_event: threading.Event) -> None:
    """Run the outbox publisher until a shutdown signal is received."""
    publisher_id = create_publisher_id()

    logger.info(
        "Outbox publisher started publisher_id=%s",
        publisher_id,
    )

    while not stop_event.is_set():
        db = SessionLocal()

        try:
            process_outbox_batch(
                db=db,
                publisher_id=publisher_id,
            )

        except Exception:
            logger.exception("Outbox batch processing failed")

        finally:
            db.close()

        stop_event.wait(POLL_INTERVAL_SECONDS)

    logger.info(
        "Outbox publisher stopped publisher_id=%s",
        publisher_id,
    )


def main() -> None:
    """Configure logging and handle graceful process shutdown."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    stop_event = threading.Event()

    def handle_shutdown(signum: int, frame: object) -> None:
        logger.info("Received shutdown signal=%s", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    run_publisher(stop_event)


if __name__ == "__main__":
    main()
