"""RabbitMQ messaging utilities for the DevOps Order Platform."""

import json
import logging
import os
from typing import Any

import pika
from pika.exceptions import AMQPError

logger = logging.getLogger(__name__)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")

ORDER_EVENTS_EXCHANGE = "order.events"
ORDER_EVENTS_QUEUE = "order.events.queue"
ORDER_EVENTS_ROUTING_KEY = "order.created"

# Retry topology
ORDER_EVENTS_RETRY_EXCHANGE = "order.events.retry"
ORDER_EVENTS_RETRY_QUEUE = "order.events.retry.queue"
ORDER_EVENTS_RETRY_ROUTING_KEY = "order.created.retry"

# Consumer retry policy
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 30
RETRY_COUNT_HEADER = "x-retry-count"


class MessagingError(RuntimeError):
    """Raised when an event cannot be published to RabbitMQ."""


def _connection_parameters() -> pika.ConnectionParameters:
    """Build RabbitMQ connection parameters from environment variables."""
    if not RABBITMQ_USER or not RABBITMQ_PASSWORD:
        raise MessagingError("RabbitMQ credentials are not configured")

    credentials = pika.PlainCredentials(
        username=RABBITMQ_USER,
        password=RABBITMQ_PASSWORD,
    )

    return pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        virtual_host=RABBITMQ_VHOST,
        credentials=credentials,
        connection_attempts=3,
        retry_delay=2,
        socket_timeout=5,
        stack_timeout=10,
        heartbeat=60,
    )


def publish_order_event(
    event: dict[str, Any],
    routing_key: str = ORDER_EVENTS_ROUTING_KEY,
) -> None:
    """Publish a persistent JSON event and wait for broker confirmation."""
    connection = None

    try:
        parameters = _connection_parameters()
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()

        channel.exchange_declare(
            exchange=ORDER_EVENTS_EXCHANGE,
            exchange_type="topic",
            durable=True,
        )

        channel.queue_declare(
            queue=ORDER_EVENTS_QUEUE,
            durable=True,
        )

        channel.queue_bind(
            exchange=ORDER_EVENTS_EXCHANGE,
            queue=ORDER_EVENTS_QUEUE,
            routing_key=routing_key,
        )

        channel.confirm_delivery()

        body = json.dumps(
            event,
            separators=(",", ":"),
            sort_keys=True,
        )

        confirmed = channel.basic_publish(
            exchange=ORDER_EVENTS_EXCHANGE,
            routing_key=routing_key,
            body=body,
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
                message_id=event.get("event_id"),
                type=event.get("event_type", "order.created"),
            ),
            mandatory=True,
        )

        if confirmed is False:
            raise MessagingError("RabbitMQ did not confirm event publication")

        logger.info(
            "RabbitMQ event published event_type=%s",
            event.get("event_type", "order.created"),
        )

    except MessagingError:
        raise

    except (AMQPError, OSError, ValueError) as exc:
        logger.exception("Failed to publish RabbitMQ event")
        raise MessagingError("Failed to publish event to RabbitMQ") from exc

    finally:
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except AMQPError:
                logger.warning(
                    "Failed to close RabbitMQ connection cleanly",
                    exc_info=True,
                )


def publish_retry_event(
    channel,
    body: bytes,
    properties: pika.BasicProperties,
    retry_count: int,
) -> None:
    """Publish a persistent retry message using an existing channel."""

    headers = dict(properties.headers or {})
    headers[RETRY_COUNT_HEADER] = retry_count

    retry_properties = pika.BasicProperties(
        content_type=properties.content_type or "application/json",
        delivery_mode=2,
        message_id=properties.message_id,
        type=properties.type,
        correlation_id=properties.correlation_id,
        headers=headers,
    )

    confirmed = channel.basic_publish(
        exchange=ORDER_EVENTS_RETRY_EXCHANGE,
        routing_key=ORDER_EVENTS_RETRY_ROUTING_KEY,
        body=body,
        properties=retry_properties,
        mandatory=True,
    )

    if confirmed is False:
        raise MessagingError("RabbitMQ did not confirm retry publication")

    logger.info(
        "Retry event published message_id=%s retry_count=%s",
        properties.message_id,
        retry_count,
    )
