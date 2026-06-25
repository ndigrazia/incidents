import os
import json
import pytest
from unittest.mock import MagicMock, patch
import requests
from datetime import datetime

from loader.soap_client import (
    RemedySOAPClient,
    RemedyClientError,
    RemedyRequestError,
    RemedyParsingError,
    load_query_time,
    save_query_time,
    append_to_history,
    increment_query_time,
    run_incident_query,
)
from loader.utils import Incident, RemedySOAPResponse

MOCK_SOAP_RESPONSE_XML = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:urn="urn:TASA_Query_Incident" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
   <soapenv:Body>
      <urn:GetListResponse>
         <urn:getListValues>
            <urn:Resumen>Incident summary test</urn:Resumen>
            <urn:Notas>Detailed notes</urn:Notas>
            <urn:Estado>Asignado</urn:Estado>
            <urn:Resolucion>Resolved issue</urn:Resolucion>
            <urn:Grupo_Propietario>Owner group</urn:Grupo_Propietario>
            <urn:Numero_Incidente>INC123</urn:Numero_Incidente>
            <urn:FechaCreacionREG>2026-06-24</urn:FechaCreacionREG>
            <urn:Vendor_Ticket_Number>VT999</urn:Vendor_Ticket_Number>
            <urn:CI>CI-1</urn:CI>
            <urn:CI_Vendor>CI-Vendor-1</urn:CI_Vendor>
            <urn:Reported_Date>2026-06-24</urn:Reported_Date>
            <urn:Grupo_Asignado>Assigned group</urn:Grupo_Asignado>
         </urn:getListValues>
         <urn:getListValues>
            <urn:Resumen>Another incident</urn:Resumen>
            <urn:Notas xsi:nil="true"/>
            <urn:Estado>Pendiente</urn:Estado>
         </urn:getListValues>
      </urn:GetListResponse>
   </soapenv:Body>
</soapenv:Envelope>"""


def test_incident_pydantic_model():
    """Test validation and field alias mapping of the Incident Pydantic model."""
    data = {
        "Resumen": "Test Summary",
        "Notas": "Some notes",
        "Estado": "Asignado",
        "Numero_Incidente": "INC001"
    }
    incident = Incident(**data)
    assert incident.resumen == "Test Summary"
    assert incident.notes == "Some notes"
    assert incident.estado == "Asignado"
    assert incident.numero_incidente == "INC001"
    
    dumped = incident.model_dump(by_alias=True)
    assert dumped["Resumen"] == "Test Summary"
    assert dumped["Notas"] == "Some notes"
    assert dumped["Estado"] == "Asignado"
    assert dumped["Numero_Incidente"] == "INC001"


def test_remedy_soap_client_init():
    """Test the initialization of RemedySOAPClient."""
    client = RemedySOAPClient(
        endpoint="https://test-remedy.com/soap",
        username="test_user",
        password="test_password",
        timeout=50,
        verify=False
    )
    assert client.endpoint == "https://test-remedy.com/soap"
    assert client.username == "test_user"
    assert client.password == "test_password"
    assert client.timeout == 50
    assert client.verify is False
    assert client.headers["Content-Type"] == "text/xml; charset=utf-8"


def test_build_envelope():
    """Test XML envelope building and qualification/username/password escaping."""
    client = RemedySOAPClient(username="foo<bar>", password="pass&word")
    qualification = "'Clase CI' = \"REF & MORE\""
    
    envelope = client._build_envelope(qualification, start_record="1", max_limit="100")
    
    # Check that escaping worked
    assert "foo" + "\x26lt;" + "bar" + "\x26gt;" in envelope
    assert "pass" + "\x26amp;" + "word" in envelope
    assert '\'Clase CI\' = "REF ' + '\x26amp;' + ' MORE"' in envelope
    assert "<urn:startRecord>1</urn:startRecord>" in envelope
    assert "<urn:maxLimit>100</urn:maxLimit>" in envelope


@patch("time.sleep", return_value=None)  # Avoid delay in retry test
def test_send_request_success(mock_sleep):
    """Test that send_request makes a session post call and returns response."""
    client = RemedySOAPClient()
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 200
    
    with patch.object(client.session, "post", return_value=mock_response) as mock_post:
        response = client._send_request("<payload/>")
        assert response == mock_response
        mock_post.assert_called_once_with(
            client.endpoint,
            data=b"<payload/>",
            headers=client.headers,
            timeout=client.timeout,
            verify=client.verify
        )


@patch("time.sleep", return_value=None)
def test_send_request_retries_and_raises(mock_sleep):
    """Test that send_request retries 3 times on RequestException and reraises."""
    client = RemedySOAPClient()
    
    with patch.object(client.session, "post", side_effect=requests.exceptions.RequestException("Conn error")) as mock_post:
        with pytest.raises(requests.exceptions.RequestException):
            client._send_request("<payload/>")
        
        # tenacity retries 3 times (1 initial + 2 retries)
        assert mock_post.call_count == 3


def test_query_incidents_success():
    """Test querying incidents with successful parsing of XML."""
    client = RemedySOAPClient()
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 200
    mock_response.text = MOCK_SOAP_RESPONSE_XML
    
    with patch.object(client, "_send_request", return_value=mock_response):
        incidents = client._query_incidents("some qualification")
        
        assert len(incidents) == 2
        assert incidents[0].resumen == "Incident summary test"
        assert incidents[0].notes == "Detailed notes"
        assert incidents[0].estado == "Asignado"
        assert incidents[0].numero_incidente == "INC123"
        assert incidents[0].grupo_asignado == "Assigned group"
        
        assert incidents[1].resumen == "Another incident"
        assert incidents[1].notes is None
        assert incidents[1].estado == "Pendiente"


def test_query_incidents_empty_and_invalid_xml():
    """Test query incidents exceptions on empty/malformed responses."""
    client = RemedySOAPClient()
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 200
    
    # 1. Empty response
    mock_response.text = ""
    with patch.object(client, "_send_request", return_value=mock_response):
        incidents = client._query_incidents("qual")
        assert incidents == []
    
    # 2. Non-XML response
    mock_response.text = "Not XML at all"
    with patch.object(client, "_send_request", return_value=mock_response):
        with pytest.raises(RemedyParsingError, match="Response content is not XML"):
            client._query_incidents("qual")
            
    # 3. Bad XML response
    mock_response.text = "<soapenv:Envelope><invalid"
    with patch.object(client, "_send_request", return_value=mock_response):
        with pytest.raises(RemedyParsingError, match="XML parsing or Pydantic validation failed"):
            client._query_incidents("qual")


def test_query_incidents_http_error():
    """Test query incidents raises RemedyRequestError on non-200 HTTP codes."""
    client = RemedySOAPClient()
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    
    with patch.object(client, "_send_request", return_value=mock_response):
        with pytest.raises(RemedyRequestError, match="Request failed with status code 500"):
            client._query_incidents("qual")


def test_get_incident_list_success():
    """Test get_incident_list high-level method returns expected valid JSON string."""
    client = RemedySOAPClient()
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 200
    mock_response.text = MOCK_SOAP_RESPONSE_XML
    
    with patch.object(client, "_send_request", return_value=mock_response):
        json_str = client.get_incident_list("qual")
        data = json.loads(json_str)
        
        assert data["ok"] is True
        assert data["text"] == ""
        assert len(data["incidents"]) == 2
        assert data["incidents"][0]["Resumen"] == "Incident summary test"


def test_get_incident_list_handled_errors():
    """Test get_incident_list handles exceptions gracefully by returning fallback JSON."""
    client = RemedySOAPClient()
    
    # RemedyRequestError handling
    with patch.object(client, "_query_incidents", side_effect=RemedyRequestError("Failed request")):
        json_str = client.get_incident_list("qual")
        data = json.loads(json_str)
        assert data["ok"] is False
        assert "Failed request" in data["text"]
        assert data["incidents"] is None
        
    # Unexpected Exception handling
    with patch.object(client, "_query_incidents", side_effect=ValueError("Unexpected crash")):
        json_str = client.get_incident_list("qual")
        data = json.loads(json_str)
        assert data["ok"] is False
        assert "Unexpected error" in data["text"]
        assert data["incidents"] is None


def test_time_state_operations(tmp_path):
    """Test load_query_time, save_query_time, append_to_history, increment_query_time."""
    state_file = tmp_path / "query_time.txt"
    history_file = tmp_path / "history.txt"
    
    # 1. load_query_time on non-existent file: should write and return current time
    loaded_time = load_query_time(str(state_file))
    current_time_prefix = datetime.now().strftime("%m/%d/%Y %H")
    assert loaded_time.startswith(current_time_prefix)
    assert state_file.read_text() == loaded_time
    
    # 2. save_query_time
    test_time = "06/24/2026 12:00"
    save_query_time(str(state_file), test_time)
    assert state_file.read_text() == test_time
    
    # 3. load_query_time on existing valid file
    loaded_time_2 = load_query_time(str(state_file))
    assert loaded_time_2 == test_time
    
    # 4. append_to_history
    append_to_history(str(history_file), test_time)
    append_to_history(str(history_file), "06/24/2026 12:15")
    history_content = history_file.read_text().splitlines()
    assert history_content == [test_time, "06/24/2026 12:15"]
    
    # 5. increment_query_time
    inc = increment_query_time("06/24/2026 12:00", 15)
    assert inc == "06/24/2026 12:15"


def test_run_incident_query_no_incidents(tmp_path):
    """Test run_incident_query when client returns no incidents."""
    state_file = tmp_path / "query_time.txt"
    history_file = tmp_path / "history.txt"
    
    initial_time = "06/24/2026 12:00"
    save_query_time(str(state_file), initial_time)
    
    client = RemedySOAPClient()
    mock_json_response = json.dumps({
        "ok": True,
        "text": "",
        "incidents": []
    })
    
    with patch.object(client, "get_incident_list", return_value=mock_json_response) as mock_get:
        run_incident_query(client, str(state_file), str(history_file))
        
        mock_get.assert_called_once_with("'Clase CI' = \"REFERENCIA\" AND '6'>\"06/24/2026 12:00\"")
        
        # Because ok is True, query time should be updated and recorded in history
        assert history_file.read_text() == f"{initial_time}\n"
        assert state_file.read_text() == "06/24/2026 12:15"


@patch("queue.azure_queue_sender.AzureQueueSender")
def test_run_incident_query_with_incidents_success(mock_azure_sender_cls, tmp_path):
    """Test run_incident_query when incidents are returned and successfully sent to Azure."""
    state_file = tmp_path / "query_time.txt"
    history_file = tmp_path / "history.txt"
    
    initial_time = "06/24/2026 12:00"
    save_query_time(str(state_file), initial_time)
    
    # Mock AzureQueueSender instance
    mock_sender_inst = MagicMock()
    mock_azure_sender_cls.return_value = mock_sender_inst
    
    client = RemedySOAPClient()
    mock_incident = {
        "Resumen": "Inc 1",
        "Notas": "Some text",
        "Estado": "Asignado",
        "Numero_Incidente": "INC111"
    }
    mock_json_response = json.dumps({
        "ok": True,
        "text": "",
        "incidents": [mock_incident]
    })
    
    with patch.object(client, "get_incident_list", return_value=mock_json_response):
        run_incident_query(client, str(state_file), str(history_file))
        
        # Verify AzureQueueSender is instantiated and used
        mock_azure_sender_cls.assert_called_once()
        mock_sender_inst.create_queue_if_not_exists.assert_called_once()
        mock_sender_inst.send_incident.assert_called_once_with(mock_incident)
        
        # Check files are updated
        assert history_file.read_text() == f"{initial_time}\n"
        assert state_file.read_text() == "06/24/2026 12:15"


@patch("queue.azure_queue_sender.AzureQueueSender")
def test_run_incident_query_with_incidents_azure_fails(mock_azure_sender_cls, tmp_path):
    """Test run_incident_query handles queue sender error by logging and NOT updating query time state."""
    state_file = tmp_path / "query_time.txt"
    history_file = tmp_path / "history.txt"
    
    initial_time = "06/24/2026 12:00"
    save_query_time(str(state_file), initial_time)
    
    # Mock AzureQueueSender to fail
    mock_sender_inst = MagicMock()
    mock_sender_inst.send_incident.side_effect = Exception("Azure queue down")
    mock_azure_sender_cls.return_value = mock_sender_inst
    
    client = RemedySOAPClient()
    mock_incident = {"Resumen": "Inc 1"}
    mock_json_response = json.dumps({
        "ok": True,
        "text": "",
        "incidents": [mock_incident]
    })
    
    with patch.object(client, "get_incident_list", return_value=mock_json_response):
        run_incident_query(client, str(state_file), str(history_file))
        
        # Query time state and history files should NOT have changed (to prevent data loss)
        assert not history_file.exists() or history_file.read_text() == ""
        assert state_file.read_text() == initial_time
