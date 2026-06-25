import os
import json
import pytest
from unittest.mock import MagicMock, patch
from azure.core.exceptions import AzureError, ResourceExistsError

from queue.azure_queue_receiver import (
    AzureQueueReceiver,
    AzureQueueReceiverError,
    TerminalMessageError
)
from loader.utils import Incident


@pytest.fixture
def mock_queue_client_class():
    with patch("queue.azure_queue_receiver.QueueClient") as mock_qc:
        yield mock_qc


def test_receiver_init_from_env(mock_queue_client_class):
    """Test that connection details are retrieved from environment variables by default."""
    env_vars = {
        "AZURE_STORAGE_CONNECTION_STRING": "DefaultConnString",
        "AZURE_INCIDENT_QUEUE_NAME": "DefaultQueueName",
        "AZURE_POISON_QUEUE_NAME": "DefaultPoisonQueue"
    }
    with patch.dict(os.environ, env_vars):
        receiver = AzureQueueReceiver()
        assert receiver.connection_string == "DefaultConnString"
        assert receiver.queue_name == "DefaultQueueName"
        assert receiver.poison_queue_name == "DefaultPoisonQueue"
        assert mock_queue_client_class.from_connection_string.call_count == 2


def test_receiver_init_explicit(mock_queue_client_class):
    """Test that explicit constructor arguments override environment variables."""
    receiver = AzureQueueReceiver(
        connection_string="ExplicitConnString",
        queue_name="ExplicitQueue",
        poison_queue_name="ExplicitPoison",
        max_retries=3,
        base64_encode=False
    )
    assert receiver.connection_string == "ExplicitConnString"
    assert receiver.queue_name == "ExplicitQueue"
    assert receiver.poison_queue_name == "ExplicitPoison"
    assert receiver.max_retries == 3
    assert mock_queue_client_class.from_connection_string.call_count == 2


def test_receiver_init_missing_raises():
    """Test that missing required parameters raises AzureQueueReceiverError."""
    with patch.dict(os.environ, {}, clear=True):
        # Missing connection string
        with pytest.raises(AzureQueueReceiverError, match="Connection String must be provided"):
            AzureQueueReceiver(queue_name="some_queue")

        # Missing queue name
        with pytest.raises(AzureQueueReceiverError, match="Queue Name must be provided"):
            AzureQueueReceiver(connection_string="some_conn")


def test_create_queues_if_not_exists_success(mock_queue_client_class):
    """Test standard successful queue creation for main and poison."""
    mock_main_client = MagicMock()
    mock_poison_client = MagicMock()
    mock_queue_client_class.from_connection_string.side_effect = [mock_main_client, mock_poison_client]
    
    receiver = AzureQueueReceiver(connection_string="conn", queue_name="queue")
    receiver.create_queues_if_not_exists()
    
    mock_main_client.create_queue.assert_called_once()
    mock_poison_client.create_queue.assert_called_once()


def test_create_queues_already_exists(mock_queue_client_class):
    """Test that ResourceExistsError is caught and handled gracefully."""
    mock_main_client = MagicMock()
    mock_main_client.create_queue.side_effect = ResourceExistsError("Already exists")
    mock_poison_client = MagicMock()
    mock_poison_client.create_queue.side_effect = ResourceExistsError("Already exists")
    mock_queue_client_class.from_connection_string.side_effect = [mock_main_client, mock_poison_client]
    
    receiver = AzureQueueReceiver(connection_string="conn", queue_name="queue")
    receiver.create_queues_if_not_exists()
    
    mock_main_client.create_queue.assert_called_once()
    mock_poison_client.create_queue.assert_called_once()


def test_parse_message_content(mock_queue_client_class):
    """Test message content parsing to Incident, dict, or str."""
    receiver = AzureQueueReceiver(connection_string="conn", queue_name="queue")
    
    # Incident JSON
    incident_data = {"Resumen": "Test Summary", "Estado": "Nuevo"}
    res = receiver.parse_message_content(json.dumps(incident_data))
    assert isinstance(res, Incident)
    assert res.resumen == "Test Summary"
    assert res.estado == "Nuevo"

    # Standard JSON dict
    generic_data = {"key": "value"}
    res = receiver.parse_message_content(json.dumps(generic_data))
    assert isinstance(res, dict)
    assert res["key"] == "value"

    # Raw String
    res = receiver.parse_message_content("Plain raw text")
    assert res == "Plain raw text"


def test_receive_and_process_success(mock_queue_client_class):
    """Test that messages are received, handler is called, and messages are deleted on success."""
    mock_main_client = MagicMock()
    mock_poison_client = MagicMock()
    mock_queue_client_class.from_connection_string.side_effect = [mock_main_client, mock_poison_client]

    mock_msg = MagicMock()
    mock_msg.id = "msg-1"
    mock_msg.content = json.dumps({"Resumen": "My incident"})
    mock_msg.dequeue_count = 1
    
    mock_main_client.receive_messages.return_value = [mock_msg]

    receiver = AzureQueueReceiver(connection_string="conn", queue_name="queue")
    
    handler = MagicMock()
    metrics = receiver.receive_and_process_messages(handler=handler)

    assert metrics["received"] == 1
    assert metrics["succeeded"] == 1
    assert metrics["failed"] == 0
    assert metrics["poisoned"] == 0

    handler.assert_called_once()
    called_arg = handler.call_args[0][0]
    assert isinstance(called_arg, Incident)
    assert called_arg.resumen == "My incident"

    mock_main_client.delete_message.assert_called_once_with(mock_msg)


def test_receive_and_process_retry_backoff(mock_queue_client_class):
    """Test that a processing failure triggers retry logic (updating visibility timeout)."""
    mock_main_client = MagicMock()
    mock_poison_client = MagicMock()
    mock_queue_client_class.from_connection_string.side_effect = [mock_main_client, mock_poison_client]

    mock_msg = MagicMock()
    mock_msg.id = "msg-fail"
    mock_msg.content = "some raw body"
    mock_msg.dequeue_count = 2
    
    mock_main_client.receive_messages.return_value = [mock_msg]

    receiver = AzureQueueReceiver(connection_string="conn", queue_name="queue", max_retries=5)
    
    handler = MagicMock(side_effect=ValueError("processing failed!"))
    metrics = receiver.receive_and_process_messages(
        handler=handler,
        retry_visibility_timeout=15
    )

    assert metrics["received"] == 1
    assert metrics["succeeded"] == 0
    assert metrics["failed"] == 1
    assert metrics["poisoned"] == 0

    handler.assert_called_once()
    # Should update visibility timeout to retry_visibility_timeout * dequeue_count (15 * 2 = 30)
    mock_main_client.update_message.assert_called_once_with(
        message=mock_msg,
        visibility_timeout=30
    )
    # Message should NOT be deleted
    mock_main_client.delete_message.assert_not_called()


def test_receive_and_process_poison_direct(mock_queue_client_class):
    """Test that a message exceeding max_retries BEFORE handler call is directly poisoned."""
    mock_main_client = MagicMock()
    mock_poison_client = MagicMock()
    mock_queue_client_class.from_connection_string.side_effect = [mock_main_client, mock_poison_client]

    mock_msg = MagicMock()
    mock_msg.id = "msg-poison"
    mock_msg.content = "poison content"
    mock_msg.dequeue_count = 6  # Exceeds max_retries of 5
    
    mock_main_client.receive_messages.return_value = [mock_msg]

    receiver = AzureQueueReceiver(connection_string="conn", queue_name="queue", max_retries=5)
    
    handler = MagicMock()
    metrics = receiver.receive_and_process_messages(handler=handler)

    assert metrics["received"] == 1
    assert metrics["succeeded"] == 0
    assert metrics["failed"] == 0
    assert metrics["poisoned"] == 1

    handler.assert_not_called()
    mock_poison_client.send_message.assert_called_once_with("poison content")
    mock_main_client.delete_message.assert_called_once_with(mock_msg)


def test_receive_and_process_poison_on_max_reached(mock_queue_client_class):
    """Test that a processing failure on the final attempt routes the message to poison queue."""
    mock_main_client = MagicMock()
    mock_poison_client = MagicMock()
    mock_queue_client_class.from_connection_string.side_effect = [mock_main_client, mock_poison_client]

    mock_msg = MagicMock()
    mock_msg.id = "msg-final-fail"
    mock_msg.content = "poison content"
    mock_msg.dequeue_count = 5  # Equals max_retries of 5
    
    mock_main_client.receive_messages.return_value = [mock_msg]

    receiver = AzureQueueReceiver(connection_string="conn", queue_name="queue", max_retries=5)
    
    handler = MagicMock(side_effect=Exception("Crash"))
    metrics = receiver.receive_and_process_messages(handler=handler)

    assert metrics["received"] == 1
    assert metrics["succeeded"] == 0
    assert metrics["failed"] == 1
    assert metrics["poisoned"] == 1

    handler.assert_called_once()
    mock_poison_client.send_message.assert_called_once_with("poison content")
    mock_main_client.delete_message.assert_called_once_with(mock_msg)


def test_receive_and_process_terminal_error(mock_queue_client_class):
    """Test that a TerminalMessageError routes message immediately to poison queue."""
    mock_main_client = MagicMock()
    mock_poison_client = MagicMock()
    mock_queue_client_class.from_connection_string.side_effect = [mock_main_client, mock_poison_client]

    mock_msg = MagicMock()
    mock_msg.id = "msg-terminal"
    mock_msg.content = "malformed json data or bad incident"
    mock_msg.dequeue_count = 1
    
    mock_main_client.receive_messages.return_value = [mock_msg]

    receiver = AzureQueueReceiver(connection_string="conn", queue_name="queue", max_retries=5)
    
    # Handler throws terminal error
    handler = MagicMock(side_effect=TerminalMessageError("Unrecoverable data format issue"))
    metrics = receiver.receive_and_process_messages(handler=handler)

    assert metrics["received"] == 1
    assert metrics["succeeded"] == 0
    assert metrics["failed"] == 0
    assert metrics["poisoned"] == 1

    handler.assert_called_once()
    mock_poison_client.send_message.assert_called_once_with("malformed json data or bad incident")
    mock_main_client.delete_message.assert_called_once_with(mock_msg)
