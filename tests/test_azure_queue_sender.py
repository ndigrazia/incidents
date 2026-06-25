import os
import json
import pytest
from unittest.mock import MagicMock, patch
from azure.core.exceptions import AzureError, ResourceExistsError

from queue.azure_queue_sender import AzureQueueSender, AzureQueueSenderError
from loader.utils import Incident


@pytest.fixture
def mock_queue_client_class():
    with patch("queue.azure_queue_sender.QueueClient") as mock_qc:
        yield mock_qc


def test_azure_queue_sender_init_from_env(mock_queue_client_class):
    """Test that connection details are retrieved from environment variables by default."""
    env_vars = {
        "AZURE_STORAGE_CONNECTION_STRING": "DefaultConnString",
        "AZURE_INCIDENT_QUEUE_NAME": "DefaultQueueName"
    }
    with patch.dict(os.environ, env_vars):
        sender = AzureQueueSender()
        assert sender.connection_string == "DefaultConnString"
        assert sender.queue_name == "DefaultQueueName"
        mock_queue_client_class.from_connection_string.assert_called_once()


def test_azure_queue_sender_init_explicit(mock_queue_client_class):
    """Test that explicit constructor arguments override environment variables."""
    sender = AzureQueueSender(
        connection_string="ExplicitConnString",
        queue_name="ExplicitQueueName",
        base64_encode=False
    )
    assert sender.connection_string == "ExplicitConnString"
    assert sender.queue_name == "ExplicitQueueName"
    mock_queue_client_class.from_connection_string.assert_called_once_with(
        conn_str="ExplicitConnString",
        queue_name="ExplicitQueueName",
        message_encode_policy=None,
        message_decode_policy=None
    )


def test_azure_queue_sender_init_missing_raises():
    """Test that missing required parameters raises AzureQueueSenderError."""
    with patch.dict(os.environ, {}, clear=True):
        # Missing connection string
        with pytest.raises(AzureQueueSenderError, match="Connection String must be provided"):
            AzureQueueSender(queue_name="some_queue")

        # Missing queue name
        with pytest.raises(AzureQueueSenderError, match="Queue Name must be provided"):
            AzureQueueSender(connection_string="some_conn")


def test_azure_queue_sender_init_exception(mock_queue_client_class):
    """Test that failures in QueueClient initialization are wrapped in AzureQueueSenderError."""
    mock_queue_client_class.from_connection_string.side_effect = Exception("Failed init")
    with pytest.raises(AzureQueueSenderError, match="Failed to initialize QueueClient"):
        AzureQueueSender(connection_string="conn", queue_name="queue")


def test_create_queue_if_not_exists_success(mock_queue_client_class):
    """Test standard successful queue creation."""
    mock_client = MagicMock()
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    sender.create_queue_if_not_exists()
    
    mock_client.create_queue.assert_called_once()


def test_create_queue_if_not_exists_already_exists(mock_queue_client_class):
    """Test that ResourceExistsError is caught and handled gracefully."""
    mock_client = MagicMock()
    mock_client.create_queue.side_effect = ResourceExistsError("Queue already exists.")
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    
    # Should not raise any error
    sender.create_queue_if_not_exists()
    mock_client.create_queue.assert_called_once()


def test_create_queue_if_not_exists_azure_error(mock_queue_client_class):
    """Test that general AzureError is wrapped in AzureQueueSenderError."""
    mock_client = MagicMock()
    mock_client.create_queue.side_effect = AzureError("Permission denied.")
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    
    with pytest.raises(AzureQueueSenderError, match="Failed to create Azure Queue: Permission denied."):
        sender.create_queue_if_not_exists()


def test_send_incident_pydantic_model(mock_queue_client_class):
    """Test sending an incident using Pydantic model (should serialize with aliases)."""
    mock_client = MagicMock()
    mock_sent_message = MagicMock()
    mock_sent_message.id = "msg-123"
    mock_sent_message.inserted_on = "2026-06-24"
    mock_sent_message.expires_on = "2026-07-01"
    mock_client.send_message.return_value = mock_sent_message
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    incident = Incident(Resumen="My Pydantic Incident", Estado="Asignado")
    
    result = sender.send_incident(incident, time_to_live=3600)
    
    assert result["id"] == "msg-123"
    assert result["inserted_on"] == "2026-06-24"
    assert result["expires_on"] == "2026-07-01"
    
    content_data = json.loads(result["content"])
    assert content_data["Resumen"] == "My Pydantic Incident"
    assert content_data["Estado"] == "Asignado"
    
    mock_client.send_message.assert_called_once_with(result["content"], time_to_live=3600)


def test_send_incident_dict(mock_queue_client_class):
    """Test sending an incident represented as a dictionary."""
    mock_client = MagicMock()
    mock_sent_message = MagicMock()
    mock_sent_message.id = "msg-456"
    mock_client.send_message.return_value = mock_sent_message
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    incident_dict = {"id": "123", "some_key": "some_val"}
    
    result = sender.send_incident(incident_dict)
    
    assert result["id"] == "msg-456"
    content_data = json.loads(result["content"])
    assert content_data["id"] == "123"
    assert content_data["some_key"] == "some_val"


def test_send_incident_string(mock_queue_client_class):
    """Test sending an incident represented as a raw string."""
    mock_client = MagicMock()
    mock_sent_message = MagicMock()
    mock_sent_message.id = "msg-789"
    mock_client.send_message.return_value = mock_sent_message
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    result = sender.send_incident("Raw string data")
    
    assert result["id"] == "msg-789"
    assert result["content"] == "Raw string data"


def test_send_incident_invalid_type(mock_queue_client_class):
    """Test that passing unsupported types raises AzureQueueSenderError."""
    mock_client = MagicMock()
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    
    with pytest.raises(AzureQueueSenderError, match="Unsupported incident type"):
        sender.send_incident(12345)  # Integer is not supported


def test_send_incident_azure_error(mock_queue_client_class):
    """Test that Azure service failures are wrapped in AzureQueueSenderError."""
    mock_client = MagicMock()
    mock_client.send_message.side_effect = AzureError("Service unavailable.")
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    
    with pytest.raises(AzureQueueSenderError, match="Azure Storage Queue error while sending message"):
        sender.send_incident("msg")


def test_send_incident_unexpected_error(mock_queue_client_class):
    """Test that unexpected generic exceptions are wrapped in AzureQueueSenderError."""
    mock_client = MagicMock()
    mock_client.send_message.side_effect = Exception("Out of memory")
    mock_queue_client_class.from_connection_string.return_value = mock_client
    
    sender = AzureQueueSender(connection_string="conn", queue_name="queue")
    
    with pytest.raises(AzureQueueSenderError, match="Unexpected error while sending message"):
        sender.send_incident("msg")
