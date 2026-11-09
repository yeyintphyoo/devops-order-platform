"""RabbitMQ consumer worker for order.created events."""

import signal
import threading

import json
import logging

import pika
from pydantic import ValidationError

from .consumer import process_order_created_event
from .database import SessionLocal
from .messaging import (
    ORDER_EVENTS_QUEUE,
    MAX_RETRIES,
    RETRY_COUNT_HEADER,
    _connection_parameters,
    publish_retry_event,
)
from .schemas import OrderCreatedEvent
from sqlalchemy.exc import SQLAlchemyError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


# Shared shutdown flag for graceful termination
shutdown_requested = threading.Event()

def get_retry_count(properties) -> int:
    """Read and validate the retry count from message headers."""

    headers = properties.headers or {}
    retry_count = headers.get(RETRY_COUNT_HEADER, 0)

    if (
        type(retry_count) is not int
        or retry_count < 0
    ):
        raise ValueError("Invalid retry count header")

    return retry_count

def handle_message(channel, method, properties, body):
    """Process one message with idempotency and bounded retries."""

    delivery_tag = method.delivery_tag

    # Step 1: Validate incoming message.
    try:
        payload = json.loads(body)
        event = OrderCreatedEvent.model_validate(payload)
        retry_count = get_retry_count(properties)

    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValidationError,
        ValueError,
        TypeError,
    ):
        logger.exception(
            "Invalid order event. Rejecting to DLQ."
        )

        channel.basic_nack(
            delivery_tag=delivery_tag,
            requeue=False,
        )
        return

    # Step 2: Process database transaction.
    try:
        with SessionLocal() as db:
            processed = process_order_created_event(
                db,
                event,
            )

    except SQLAlchemyError:
        logger.exception(
            "Database failure event_id=%s retry_count=%s",
            event.event_id,
            retry_count,
        )

        # Step 3: Check retry limit.
        if retry_count >= MAX_RETRIES:
            logger.error(
                "Retry limit exceeded event_id=%s. Sending to DLQ.",
                event.event_id,
            )

            channel.basic_nack(
                delivery_tag=delivery_tag,
                requeue=False,
            )
            return

        # Step 4: Publish retry before ACKing original.
        try:
            publish_retry_event(
                channel=channel,
                body=body,
                properties=properties,
                retry_count=retry_count + 1,
            )

        except Exception:
            logger.exception(
                "Retry publication failed. Original message must remain unacknowledged."
            )

            # Do not ACK the original.
            # Propagate the error so the worker exits and
            # RabbitMQ can redeliver after connection closure.
            raise

        # Step 5: ACK original only after retry publication succeeds.
        channel.basic_ack(
            delivery_tag=delivery_tag,
        )

        logger.info(
            "Event scheduled for retry event_id=%s retry_count=%s",
            event.event_id,
            retry_count + 1,
        )
        return

    # Step 6: Successful or duplicate processing.
    channel.basic_ack(
        delivery_tag=delivery_tag,
    )

    if processed:
        logger.info(
            "Order event processed event_id=%s order_id=%s",
            event.event_id,
            event.order_id,
        )
    else:
        logger.info(
            "Duplicate event acknowledged event_id=%s",
            event.event_id,
        )

def handle_shutdown_signal(signum, frame):
    """Request graceful consumer shutdown."""
    shutdown_requested.set()

def main():
    """Start the RabbitMQ consumer."""

    connection = None
    shutdown_requested.clear()
    previous_sigterm_handler = signal.signal(
       signal.SIGTERM,
       handle_shutdown_signal,
    )
    try:
        connection = pika.BlockingConnection(
            _connection_parameters()
        )

        channel = connection.channel()

        channel.confirm_delivery()

        # Consume only one unacknowledged message at a time.
        channel.basic_qos(
            prefetch_count=1,
        )

        channel.basic_consume(
            queue=ORDER_EVENTS_QUEUE,
            on_message_callback=handle_message,
            auto_ack=False,
        )

        logger.info(
            "Consumer started. Waiting for messages on %s",
            ORDER_EVENTS_QUEUE,
        )

        while not shutdown_requested.is_set():
           connection.process_data_events(time_limit=1)
    except KeyboardInterrupt:
        logger.info(
            "Consumer interrupted. Shutting down gracefully."
        )

    finally:
       signal.signal(
          signal.SIGTERM,
          previous_sigterm_handler,
        )

       if connection is not None and connection.is_open:
            connection.close()
            logger.info("RabbitMQ connection closed.")


if __name__ == "__main__":
    main()