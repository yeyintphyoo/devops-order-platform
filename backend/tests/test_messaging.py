import json
from unittest.mock import MagicMock, Mock, patch


import pika
import pytest

from backend.app.messaging import (
    MessagingError,
    ORDER_EVENTS_EXCHANGE,
    ORDER_EVENTS_QUEUE,
    ORDER_EVENTS_ROUTING_KEY,
    _connection_parameters,
    publish_order_event,
    ORDER_EVENTS_RETRY_EXCHANGE,
    ORDER_EVENTS_RETRY_ROUTING_KEY,
    RETRY_COUNT_HEADER,
    publish_retry_event,
)


@pytest.fixture
def rabbitmq_env(monkeypatch):
    monkeypatch.setattr("backend.app.messaging.RABBITMQ_HOST", "rabbitmq")
    monkeypatch.setattr("backend.app.messaging.RABBITMQ_PORT", 5672)
    monkeypatch.setattr("backend.app.messaging.RABBITMQ_USER", "test-user")
    monkeypatch.setattr("backend.app.messaging.RABBITMQ_PASSWORD", "test-password")
    monkeypatch.setattr("backend.app.messaging.RABBITMQ_VHOST", "/")


def test_connection_parameters_require_credentials(monkeypatch):
    monkeypatch.setattr("backend.app.messaging.RABBITMQ_USER", "")
    monkeypatch.setattr("backend.app.messaging.RABBITMQ_PASSWORD", "")

    with pytest.raises(
        MessagingError,
        match="credentials are not configured",
    ):
        _connection_parameters()


def test_connection_parameters_use_configured_values(rabbitmq_env):
    parameters = _connection_parameters()

    assert parameters.host == "rabbitmq"
    assert parameters.port == 5672
    assert parameters.virtual_host == "/"
    assert parameters.credentials.username == "test-user"


def test_publish_order_event_success(rabbitmq_env):
    event = {
        "event_id": "test-event-1",
        "event_type": "order.created",
        "order_id": 123,
    }

    mock_connection = MagicMock()
    mock_connection.is_open = True
    mock_channel = mock_connection.channel.return_value
    mock_channel.basic_publish.return_value = True

    with patch(
        "backend.app.messaging.pika.BlockingConnection",
        return_value=mock_connection,
    ):
        publish_order_event(event)

    mock_connection.channel.assert_called_once_with()

    mock_channel.exchange_declare.assert_called_once_with(
        exchange=ORDER_EVENTS_EXCHANGE,
        exchange_type="topic",
        durable=True,
    )

    mock_channel.queue_declare.assert_called_once_with(
        queue=ORDER_EVENTS_QUEUE,
        durable=True,
    )

    mock_channel.queue_bind.assert_called_once_with(
        exchange=ORDER_EVENTS_EXCHANGE,
        queue=ORDER_EVENTS_QUEUE,
        routing_key=ORDER_EVENTS_ROUTING_KEY,
    )

    mock_channel.confirm_delivery.assert_called_once_with()

    publish_call = mock_channel.basic_publish.call_args
    assert publish_call.kwargs["exchange"] == ORDER_EVENTS_EXCHANGE
    assert publish_call.kwargs["routing_key"] == ORDER_EVENTS_ROUTING_KEY
    assert publish_call.kwargs["mandatory"] is True
    assert json.loads(publish_call.kwargs["body"]) == event
    assert publish_call.kwargs["properties"].delivery_mode == 2
    assert publish_call.kwargs["properties"].content_type == "application/json"
    assert publish_call.kwargs["properties"].message_id == "test-event-1"

    mock_connection.close.assert_called_once_with()


def test_publish_order_event_connection_failure(rabbitmq_env):
    with patch(
        "backend.app.messaging.pika.BlockingConnection",
        side_effect=pika.exceptions.AMQPConnectionError(),
    ):
        with pytest.raises(
            MessagingError,
            match="Failed to publish event",
        ):
            publish_order_event({"event_type": "order.created"})


def test_publish_order_event_closes_connection_after_publish_error(
    rabbitmq_env,
):
    mock_connection = MagicMock()
    mock_connection.is_open = True
    mock_channel = mock_connection.channel.return_value
    mock_channel.basic_publish.side_effect = pika.exceptions.AMQPError()

    with patch(
        "backend.app.messaging.pika.BlockingConnection",
        return_value=mock_connection,
    ):
        with pytest.raises(MessagingError):
            publish_order_event({"event_type": "order.created"})

    mock_connection.close.assert_called_once_with()


def test_publish_retry_event_success():
    channel = Mock()
    channel.basic_publish.return_value = True

    body = b'{"event_id":"123","event_type":"order.created"}'

    properties = pika.BasicProperties(
        content_type="application/json",
        message_id="123",
        type="order.created",
        headers={"trace-id": "abc"},
    )

    publish_retry_event(
        channel=channel,
        body=body,
        properties=properties,
        retry_count=1,
    )

    channel.basic_publish.assert_called_once()

    kwargs = channel.basic_publish.call_args.kwargs

    assert kwargs["exchange"] == ORDER_EVENTS_RETRY_EXCHANGE
    assert kwargs["routing_key"] == ORDER_EVENTS_RETRY_ROUTING_KEY
    assert kwargs["body"] == body
    assert kwargs["mandatory"] is True

    retry_properties = kwargs["properties"]

    assert retry_properties.delivery_mode == 2
    assert retry_properties.message_id == "123"
    assert retry_properties.headers[RETRY_COUNT_HEADER] == 1
    assert retry_properties.headers["trace-id"] == "abc"


def test_publish_retry_event_not_confirmed():
    channel = Mock()
    channel.basic_publish.return_value = False

    properties = pika.BasicProperties(
        message_id="123",
    )

    with pytest.raises(MessagingError):
        publish_retry_event(
            channel=channel,
            body=b"test",
            properties=properties,
            retry_count=1,
        )
