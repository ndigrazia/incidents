import os
import requests
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
import logging
import sys
import json
from typing import Optional, List
from datetime import datetime, timedelta
from loader.utils import Incident, RemedySOAPResponse
from dotenv import load_dotenv

# Configure a simple logger to display retry progress
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

logger = logging.getLogger(__name__)

# Load environment variables from .env file if present
load_dotenv()

# Defaults loaded from environment variables with fallback values
SOAP_URL = os.environ.get(
    "REMEDY_SOAP_URL",
    "https://remedyaverias20.movistar.com.ar:8443/arsys/services/ARService?server=aparrdyresp101&webService=TASA_Query_Incident"
)
USERNAME = os.environ.get("REMEDY_USERNAME", "REMEDYIAGOB")
PASSWORD = os.environ.get("REMEDY_PASSWORD", "12345")

HISTORY_FILE = os.environ.get("REMEDY_HISTORY_FILE", "out/query_times_history.txt")
STATE_FILE = os.environ.get("REMEDY_TIME_FILE", "conf/query_time.txt")

TIMEOUT = int(os.environ.get("REMEDY_TIMEOUT", 100))

class RemedyClientError(Exception):
    """Base exception for all Remedy SOAP Client errors."""
    pass


class RemedyRequestError(RemedyClientError):
    """Exception raised for connection and HTTP request failures."""
    pass


class RemedyParsingError(RemedyClientError):
    """Exception raised when XML response parsing fails."""
    pass




class RemedySOAPClient:
    """
    A service client to interact with BMC Remedy TASA_Query_Incident SOAP service.
    """
    def __init__(
        self,
        endpoint: str = SOAP_URL,
        username: str = USERNAME,
        password: str = PASSWORD,
        timeout: float = 100,
        verify: bool = True
    ):
        self.endpoint = endpoint
        self.username = username
        self.password = password
        self.timeout = timeout
        self.verify = verify
        self.headers = {
            'Content-Type': 'text/xml; charset=utf-8',
            'SOAPAction': 'urn:TASA_Query_Incident/GetList'
        }
        # Use requests.Session for connection pooling (Keep-Alive)
        self.session = requests.Session()

    def _build_envelope(self, qualification: str, start_record: str = "", max_limit: str = "") -> str:
        """
        Builds the SOAP Envelope payload matching the required BMC Remedy schema.
        Safely escapes user input to prevent malformed XML and SOAP injection.
        """
        safe_username = escape(self.username)
        safe_password = escape(self.password)
        safe_qualification = escape(qualification)
        safe_start = escape(str(start_record))
        safe_limit = escape(str(max_limit))

        envelope = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:urn="urn:TASA_Query_Incident">
        <soapenv:Header>
            <urn:AuthenticationInfo>
                <urn:userName>{safe_username}</urn:userName>
                <urn:password>{safe_password}</urn:password>
            </urn:AuthenticationInfo>
        </soapenv:Header>
        <soapenv:Body>
            <urn:GetList>
                <urn:Qualification>{safe_qualification}</urn:Qualification>
                <urn:startRecord>{safe_start}</urn:startRecord>
                <urn:maxLimit>{safe_limit}</urn:maxLimit>
            </urn:GetList>
        </soapenv:Body>
        </soapenv:Envelope>"""
        return envelope.strip()

    # Apply tenacity retry logic
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        before_sleep=before_sleep_log(logger, logging.INFO),
        reraise=True
    )
    def _send_request(self, payload: str) -> requests.Response:
        """Helper to send POST request, which is decorated with tenacity retry."""
        return self.session.post(
            self.endpoint,
            data=payload.encode('utf-8'),
            headers=self.headers,
            timeout=self.timeout,
            verify=self.verify
        )

    def _query_incidents(self, qualification: str, start_record: str = "", max_limit: str = "") -> List[Incident]:
        """
        Queries the Remedy SOAP API and returns a list of parsed Incident Pydantic models.
        
        Args:
            qualification (str): The query qualification filter.
            start_record (str): Starting record index.
            max_limit (str): Maximum number of records to return.
            
        Returns:
            List[Incident]: The parsed incident list.
            
        Raises:
            RemedyRequestError: If connection/HTTP requests fail.
            RemedyParsingError: If XML response format is malformed or invalid.
        """
        payload = self._build_envelope(qualification, start_record, max_limit)
        try:
            response = self._send_request(payload)
            if response.status_code != 200:
                raise RemedyRequestError(f"Request failed with status code {response.status_code}: {response.text}")
            text = response.text
        except requests.exceptions.RequestException as e:
            raise RemedyRequestError(f"Connection/Request Exception after retries: {str(e)}") from e

        # Parse XML response
        incidents = []
        try:
            xml_text = text.strip() if text else ""
            if not xml_text:
                return incidents

            if not xml_text.startswith("<"):
                raise RemedyParsingError("Response content is not XML")

            root = ET.fromstring(xml_text)
            body = root.find(".//{*}Body")
            if body is not None and len(body) > 0:
                response_payload = body[0]
                list_values_elements = response_payload.findall(".//{*}getListValues")
                for elem in list_values_elements:
                    incident_data = {}
                    for child in elem:
                        tag_name = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
                            incident_data[tag_name] = None
                        else:
                            incident_data[tag_name] = child.text
                    incidents.append(Incident(**incident_data))
        except Exception as e:
            if isinstance(e, RemedyParsingError):
                raise
            raise RemedyParsingError(f"XML parsing or Pydantic validation failed: {str(e)}") from e

        return incidents

    def get_incident_list(self, qualification: str, start_record: str = "", max_limit: str = "") -> str:
        """
        Sends the SOAP query to the TASA_Query_Incident service, parses the XML response,
        and returns a JSON string as a response. Maintain backward-compatibility.
        
        Args:
            qualification (str): The query qualification.
            start_record (str): Starting record index.
            max_limit (str): Maximum number of records to return.

        Returns:
            str: JSON response representing the status and list of parsed incidents.
        """
        try:
            incidents = self._query_incidents(qualification, start_record, max_limit)
            soap_response = RemedySOAPResponse(incidents=incidents)
            return json.dumps({
                "ok": True,
                "text": "",
                "incidents": [inc.model_dump(by_alias=True) for inc in soap_response.incidents]
            }, indent=2)
        except RemedyClientError as e:
            logger.error(str(e))
            return json.dumps({
                "ok": False,
                "text": str(e),
                "incidents": None
            }, indent=2)
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return json.dumps({
                "ok": False,
                "text": f"Unexpected error: {str(e)}",
                "incidents": None
            }, indent=2)


def load_query_time(state_file: str) -> str:
    """
    Loads the query time from the state file if it exists and is non-empty.
    Otherwise, returns the current time formatted and initializes the state file.
    """
    default_time = datetime.now().strftime("%m/%d/%Y %H:%M")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                loaded = f.read().strip()
                if loaded:
                    return loaded
        except Exception:
            pass
    
    # If file doesn't exist, is empty, or there was a read error, write default and return it
    save_query_time(state_file, default_time)
    return default_time


def save_query_time(state_file: str, query_time: str) -> None:
    """Saves the specified query time to the state file."""
    # Ensure directory exists if path contains directories
    dir_name = os.path.dirname(state_file)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(state_file, "w") as f:
        f.write(query_time)


def append_to_history(history_file: str, query_time: str) -> None:
    """Appends the query time to the history file."""
    # Ensure directory exists if path contains directories
    dir_name = os.path.dirname(history_file)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(history_file, "a") as f:
        f.write(query_time + "\n")


def increment_query_time(query_time: str, minutes: int = 15) -> str:
    """Increments the query time by the specified number of minutes and returns it formatted."""
    dt = datetime.strptime(query_time, "%m/%d/%Y %H:%M") + timedelta(minutes=minutes)
    return dt.strftime("%m/%d/%Y %H:%M")


def run_incident_query(client: RemedySOAPClient, state_file: str, history_file: str) -> None:
    """
    Loads query time state, retrieves incidents using qualification filter,
    prints output, and updates state/history files on success.
    """
    query_time = load_query_time(state_file)
    qualification = f"'Clase CI' = \"REFERENCIA\" AND '6'>\"{query_time}\""
    logger.info(f"Calling SOAP service with qualification: {qualification}")
    
    json_response_str = client.get_incident_list(qualification)
    response_data = json.loads(json_response_str)
    
    if response_data.get("ok"):
        incidents = response_data.get("incidents", [])
        
        if incidents:
            from queue.azure_queue_sender import AzureQueueSender
            try:
                queue_sender = AzureQueueSender()
                queue_sender.create_queue_if_not_exists()
                for incident in incidents:
                    logger.info(json.dumps(incident, indent=2))
                    queue_sender.send_incident(incident)
            except Exception as e:
                logger.error(f"Error sending incidents to Azure Queue: {str(e)}")
                # Do not update the query time if queue sending fails to avoid data loss
                return

        logger.info(f"Incidents: {len(incidents)}")

        # Save successful query time value in the history file
        append_to_history(history_file, query_time)

        # Update its value by adding 15 minutes and save
        updated_time = increment_query_time(query_time, 15)
        save_query_time(state_file, updated_time)
    else:
        logger.error(f"Error: {response_data.get('text')}")


def main() -> None:
    """Main entry point to execute the Remedy SOAP Client routine."""
    try:
        client = RemedySOAPClient(timeout=TIMEOUT)
        run_incident_query(client, STATE_FILE, HISTORY_FILE)
    except Exception as e:
        logger.error(f"Error: {str(e)}")


if __name__ == "__main__":
    main()
