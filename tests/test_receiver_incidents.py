import pytest
from unittest.mock import MagicMock, patch
from loader.utils import Incident
from receiver_incidents import process_incident, get_sender


@pytest.fixture
def mock_summarizer():
    with patch("receiver_incidents.summarizer") as mock_sum:
        yield mock_sum


@pytest.fixture
def mock_get_sender():
    with patch("receiver_incidents.get_sender") as mock_gs:
        mock_sender_instance = MagicMock()
        mock_gs.return_value = mock_sender_instance
        yield mock_sender_instance


def test_process_incident_with_summary(mock_summarizer, mock_get_sender):
    """Test process_incident when a summary is successfully generated."""
    incident = Incident(
        Numero_Incidente="INC001",
        Estado="Asignado",
        Notas="DB Connection timeout on server 1."
    )
    mock_summarizer.summarize.return_value = "Mocked DB timeout summary"

    process_incident(incident)

    mock_summarizer.summarize.assert_called_once_with("DB Connection timeout on server 1.")
    mock_get_sender.send_incident.assert_called_once_with("Incident INC001: Mocked DB timeout summary")


def test_process_incident_with_no_notes(mock_summarizer, mock_get_sender):
    """Test process_incident when incident notes are missing or empty."""
    incident = Incident(
        Numero_Incidente="INC002",
        Estado="Asignado",
        Notas=None
    )
    mock_summarizer.summarize.return_value = "No notes provided to summarize."

    process_incident(incident)

    mock_summarizer.summarize.assert_called_once_with(None)
    mock_get_sender.send_incident.assert_called_once_with("Incident INC002: No comment")


def test_process_incident_dict_with_notes(mock_summarizer, mock_get_sender):
    """Test process_incident when receiving a dictionary containing notes."""
    incident_dict = {
        "Numero_Incidente": "INC003",
        "notes": "Memory leak detected."
    }
    mock_summarizer.summarize.return_value = "Mocked memory leak summary"

    process_incident(incident_dict)

    mock_summarizer.summarize.assert_called_once_with("Memory leak detected.")
    mock_get_sender.send_incident.assert_called_once_with("Incident INC003: Mocked memory leak summary")


def test_process_incident_dict_without_notes(mock_summarizer, mock_get_sender):
    """Test process_incident when receiving a dictionary without notes."""
    incident_dict = {
        "Numero_Incidente": "INC004"
    }

    process_incident(incident_dict)

    mock_summarizer.summarize.assert_not_called()
    mock_get_sender.send_incident.assert_called_once_with("Incident INC004: No comment")


def test_process_incident_raw_string(mock_summarizer, mock_get_sender):
    """Test process_incident when receiving a raw string."""
    process_incident("Some raw log data")

    mock_summarizer.summarize.assert_not_called()
    mock_get_sender.send_incident.assert_called_once_with("No comment")


def test_process_incident_summarizer_failure(mock_summarizer, mock_get_sender):
    """Test process_incident when summarizer fails, it should fallback to sending 'No comment'."""
    incident = Incident(
        Numero_Incidente="INC005",
        Estado="Asignado",
        Notas="Some notes"
    )
    mock_summarizer.summarize.side_effect = Exception("LLM crash")

    process_incident(incident)

    mock_summarizer.summarize.assert_called_once_with("Some notes")
    mock_get_sender.send_incident.assert_called_once_with("Incident INC005: No comment")


def test_get_sender_custom_queue_env():
    """Test that get_sender retrieves the custom queue name from env variables."""
    import receiver_incidents
    import os

    # Clear cached sender
    receiver_incidents.sender = None

    env_vars = {
        "AZURE_STORAGE_CONNECTION_STRING": "test-connection-string",
        "AZURE_SUMMARIES_QUEUE_NAME": "custom-summaries-queue-name",
    }
    with patch.dict(os.environ, env_vars), patch("receiver_incidents.AzureQueueSender") as mock_sender_class:
        get_sender()
        mock_sender_class.assert_called_once_with(queue_name="custom-summaries-queue-name")

    # Clear cached sender again to clean up
    receiver_incidents.sender = None
