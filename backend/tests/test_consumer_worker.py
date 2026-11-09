import json
from unittest.mock import Mock, patch
from uuid import uuid4

import pika
import pytest

import signal

from sqlalchemy.exc import OperationalError
from backend.app.consumer_worker import main

from backend.app.consumer_worker import (
    get_retry_count,
    handle_shutdown_signal,
    handle_message,main,
)
def make_event():
    """Create a valid order.created event."""

    return {
        "event_id": str(uuid4()),
        "event_type": "order.created",
        "order_id": 1,
        "product_id": 1,
        "product_name": "Laptop",
        "quantity": 1,
        "unit_price": "2500.00",
        "total_price": "2500.00",
        "status": "created",
        "created_at": "2026-11-04T10:00:00",
    }


def make_message(retry_count=0):
    channel = Mock()

    method = Mock()
    method.delivery_tag = 123

    properties = pika.BasicProperties(
        message_id=str(uuid4()),
        headers={"x-retry-count": retry_count},
    )

    body = json.dumps(make_event()).encode()

    return channel, method, properties, body

def database_failure():
    return OperationalError(
        "SELECT 1",
        {},
        Exception("PostgreSQL unavailable"),
    )


def test_consumer_ack_after_success():
    channel, method, properties, body = make_message()

    with patch(
        "backend.app.consumer_worker.SessionLocal"
    ) as mock_session, patch(
        "backend.app.consumer_worker.process_order_created_event",
        return_value=True,
    ):
        handle_message(channel, method, properties, body)

    channel.basic_ack.assert_called_once_with(
        delivery_tag=123
    )

    channel.basic_nack.assert_not_called()


def test_consumer_ack_duplicate_event():
    channel, method, properties, body = make_message()

    with patch(
        "backend.app.consumer_worker.SessionLocal"
    ), patch(
        "backend.app.consumer_worker.process_order_created_event",
        return_value=False,
    ):
        handle_message(channel, method, properties, body)

    channel.basic_ack.assert_called_once_with(
        delivery_tag=123
    )


def test_consumer_reject_invalid_json():
    channel, method, properties, body = make_message()

    handle_message(
        channel,
        method,
        properties,
        b"invalid-json",
    )

    channel.basic_nack.assert_called_once_with(
        delivery_tag=123,
        requeue=False,
    )

    channel.basic_ack.assert_not_called()


def test_consumer_publish_retry_after_database_failure():
    channel, method, properties, body = make_message(
        retry_count=0
    )

    with patch(
        "backend.app.consumer_worker.SessionLocal"
    ), patch(
        "backend.app.consumer_worker.process_order_created_event",
        side_effect=database_failure(),
    ), patch(
        "backend.app.consumer_worker.publish_retry_event"
    ) as mock_retry:

        handle_message(channel, method, properties, body)

    mock_retry.assert_called_once_with(
        channel=channel,
        body=body,
        properties=properties,
        retry_count=1,
    )

    channel.basic_ack.assert_called_once_with(
        delivery_tag=123
    )


def test_consumer_reject_after_max_retries():
    channel, method, properties, body = make_message(
        retry_count=3
    )

    with patch(
        "backend.app.consumer_worker.SessionLocal"
    ), patch(
        "backend.app.consumer_worker.process_order_created_event",
        side_effect=database_failure(),
    ), patch(
        "backend.app.consumer_worker.publish_retry_event"
    ) as mock_retry:

        handle_message(channel, method, properties, body)

    mock_retry.assert_not_called()

    channel.basic_nack.assert_called_once_with(
        delivery_tag=123,
        requeue=False,
    )

    channel.basic_ack.assert_not_called()


def test_consumer_does_not_ack_when_retry_publish_fails():
    channel, method, properties, body = make_message()

    with patch(
        "backend.app.consumer_worker.SessionLocal"
    ), patch(
        "backend.app.consumer_worker.process_order_created_event",
        side_effect=database_failure(),
    ), patch(
        "backend.app.consumer_worker.publish_retry_event",
        side_effect=RuntimeError("RabbitMQ unavailable"),
    ):

        with pytest.raises(RuntimeError):
            handle_message(
                channel,
                method,
                properties,
                body,
            )

    channel.basic_ack.assert_not_called()
    channel.basic_nack.assert_not_called()


@pytest.mark.parametrize(
    "invalid_count",
    [-1, "abc", None, 1.5, True],
)

def test_invalid_retry_count(invalid_count):
    properties = pika.BasicProperties(
        headers={"x-retry-count": invalid_count}
    )

    with pytest.raises(ValueError):
        get_retry_count(properties)

def test_consumer_closes_connection_after_failure():
    connection = Mock()
    connection.is_open = True

    channel = connection.channel.return_value

    connection.process_data_events.side_effect = RuntimeError(
    "Consumer processing failed"
    )

    with patch(
        "backend.app.consumer_worker._connection_parameters"
    ), patch(
        "backend.app.consumer_worker.pika.BlockingConnection",
        return_value=connection,
    ):
        with pytest.raises(
            RuntimeError,
            match="Consumer processing failed",
        ):
            main()

    channel.confirm_delivery.assert_called_once()
    channel.basic_qos.assert_called_once_with(
        prefetch_count=1
    )

    channel.basic_consume.assert_called_once()

    connection.close.assert_called_once()

def test_consumer_handles_keyboard_interrupt():
    connection = Mock()
    connection.is_open = True

    channel = connection.channel.return_value

    # Simulate Ctrl+C during AMQP event processing.
    connection.process_data_events.side_effect = KeyboardInterrupt()

    with patch(
        "backend.app.consumer_worker._connection_parameters"
    ), patch(
        "backend.app.consumer_worker.pika.BlockingConnection",
        return_value=connection,
    ):
        main()

    channel.confirm_delivery.assert_called_once()

    channel.basic_qos.assert_called_once_with(
        prefetch_count=1
    )

    channel.basic_consume.assert_called_once()

    connection.process_data_events.assert_called_once_with(
        time_limit=1
    )

    connection.close.assert_called_once()


def test_consumer_does_not_close_already_closed_connection():
    connection = Mock()
    connection.is_open = False

    # Simulate a failure during AMQP event processing.
    connection.process_data_events.side_effect = RuntimeError(
        "Connection lost"
    )

    with patch(
        "backend.app.consumer_worker._connection_parameters"
    ), patch(
        "backend.app.consumer_worker.pika.BlockingConnection",
        return_value=connection,
    ):
        with pytest.raises(
            RuntimeError,
            match="Connection lost",
        ):
            main()

    connection.process_data_events.assert_called_once_with(
        time_limit=1
    )

    connection.close.assert_not_called()

def test_consumer_handles_sigterm():
    connection = Mock()
    connection.is_open = True

    channel = connection.channel.return_value

    def simulate_sigterm(*args, **kwargs):
        # Simulate SIGTERM without sending a real OS signal.
        handle_shutdown_signal(signal.SIGTERM, None)

    connection.process_data_events.side_effect = simulate_sigterm

    with patch(
        "backend.app.consumer_worker._connection_parameters"
    ), patch(
        "backend.app.consumer_worker.pika.BlockingConnection",
        return_value=connection,
    ):
        main()

    channel.basic_consume.assert_called_once()

    connection.process_data_events.assert_called_once_with(
        time_limit=1
    )

    connection.close.assert_called_once()