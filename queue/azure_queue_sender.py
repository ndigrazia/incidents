import os
import json
import logging
import sys
from typing import Union, Dict, Any, Optional
from azure.storage.queue import QueueClient, TextBase64EncodePolicy, TextBase64DecodePolicy
from azure.core.exceptions import AzureError
from dotenv import load_dotenv

from loader.utils import Incident

# Configure a simple logger to display retry progress
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

logger = logging.getLogger(__name__)

load_dotenv()  # Load environment variables from .env file if present


class AzureQueueSenderError(Exception):
    """Base exception for all Azure Queue Sender errors."""
    pass


class AzureQueueSender:
    """
    A client to send incidents to an Azure Storage Queue.
    Supports Pydantic Incident models, dictionaries, and raw strings.
    Automatically encodes messages in Base64 (standard for Azure Storage Queues).
    """
    def __init__(
        self,
        connection_string: Optional[str] = None,
        queue_name: Optional[str] = None,
        base64_encode: bool = True
    ):
        """
        Initializes the AzureQueueSender with connection details.
        
        Args:
            connection_string (str, optional): Connection string to the Azure Storage Account.
                Defaults to the 'AZURE_STORAGE_CONNECTION_STRING' environment variable.
            queue_name (str, optional): The name of the Azure Storage Queue.
                Defaults to the 'AZURE_INCIDENT_QUEUE_NAME' environment variable.
            base64_encode (bool): Whether to encode queue messages in Base64. Defaults to True.
        """
        self.connection_string = connection_string or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        self.queue_name = queue_name or os.environ.get("AZURE_INCIDENT_QUEUE_NAME")

        if not self.connection_string:
            raise AzureQueueSenderError(
                "Azure Storage Connection String must be provided or set in the "
                "'AZURE_STORAGE_CONNECTION_STRING' environment variable."
            )
        if not self.queue_name:
            raise AzureQueueSenderError(
                "Queue Name must be provided or set in the "
                "'AZURE_INCIDENT_QUEUE_NAME' environment variable."
            )

        encode_policy = TextBase64EncodePolicy() if base64_encode else None
        decode_policy = TextBase64DecodePolicy() if base64_encode else None

        try:
            self.queue_client = QueueClient.from_connection_string(
                conn_str=self.connection_string,
                queue_name=self.queue_name,
                message_encode_policy=encode_policy,
                message_decode_policy=decode_policy
            )
        except Exception as e:
            raise AzureQueueSenderError(f"Failed to initialize QueueClient: {str(e)}") from e

    def create_queue_if_not_exists(self) -> None:
        """
        Creates the target storage queue if it does not already exist.
        
        Raises:
            AzureQueueSenderError: If there is an Azure service exception while creating the queue.
        """
        from azure.core.exceptions import ResourceExistsError
        try:
            logger.info(f"Ensuring Azure Storage Queue '{self.queue_name}' exists...")
            self.queue_client.create_queue()
            logger.info(f"Queue '{self.queue_name}' created successfully.")
        except ResourceExistsError:
            logger.info(f"Queue '{self.queue_name}' already exists.")
        except AzureError as e:
            raise AzureQueueSenderError(f"Failed to create Azure Queue: {str(e)}") from e

    def send_incident(self, incident: Union[Incident, Dict[str, Any], str], **kwargs: Any) -> Dict[str, Any]:
        """
        Sends an incident to the configured Azure Storage Queue.
        
        Args:
            incident (Incident | dict | str): The incident data to send.
                If it's an Incident (Pydantic) model or dict, it will be serialized to JSON.
                If it's a string, it will be sent as-is.
            **kwargs: Additional parameters passed directly to queue_client.send_message
                (e.g., time_to_live, visibility_timeout).
                
        Returns:
            dict: A dictionary containing details of the sent message (id, insertion_time, expiration_time).
            
        Raises:
            AzureQueueSenderError: If serialization fails or Azure fails to queue the message.
        """
        # 1. Serialize the incident
        if isinstance(incident, Incident):
            try:
                # Use model_dump_json or model_dump depending on desired serialization
                message_content = incident.model_dump_json(by_alias=True)
            except Exception as e:
                raise AzureQueueSenderError(f"Failed to serialize Incident model: {str(e)}") from e
        elif isinstance(incident, dict):
            try:
                message_content = json.dumps(incident)
            except Exception as e:
                raise AzureQueueSenderError(f"Failed to serialize incident dictionary to JSON: {str(e)}") from e
        elif isinstance(incident, str):
            message_content = incident
        else:
            raise AzureQueueSenderError(
                f"Unsupported incident type: {type(incident)}. Must be Incident, dict, or str."
            )

        # 2. Send the message to the queue
        try:
            logger.info(f"Sending incident message to queue '{self.queue_name}'...")
            sent_message = self.queue_client.send_message(message_content, **kwargs)
            logger.info(f"Successfully sent incident. Message ID: {sent_message.id}")
            
            return {
                "id": sent_message.id,
                "inserted_on": getattr(sent_message, "inserted_on", None),
                "expires_on": getattr(sent_message, "expires_on", None),
                "content": message_content
            }
        except AzureError as e:
            raise AzureQueueSenderError(f"Azure Storage Queue error while sending message: {str(e)}") from e
        except Exception as e:
            raise AzureQueueSenderError(f"Unexpected error while sending message: {str(e)}") from e

#if __name__ == "__main__":
#    try:
#        sender = AzureQueueSender()
#        sender.create_queue_if_not_exists()
#        
#        # Example incident data
#        incident_data = {
#            "id": "12345",
#            "type": "example_incident",
#            "description": "This is a test incident."
#        }
#        
#        result = sender.send_incident(incident_data)
#        print(f"Message sent successfully: {result}")
#    except AzureQueueSenderError as e:
#        print(f"Failed to execute Azure Queue operation: {str(e)}")
#