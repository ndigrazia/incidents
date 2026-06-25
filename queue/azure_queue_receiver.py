import os
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Union, Dict, Any, Optional, Callable
from azure.storage.queue import QueueClient, TextBase64EncodePolicy, TextBase64DecodePolicy
from azure.core.exceptions import AzureError
from dotenv import load_dotenv

from loader.utils import Incident

# Configure a simple logger to display progress
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()  # Load environment variables from .env file if present


class AzureQueueReceiverError(Exception):
    """Base exception for all Azure Queue Receiver errors."""
    pass


class TerminalMessageError(Exception):
    """Exception indicating that the message has a terminal processing failure and should not be retried."""
    pass


class AzureQueueReceiver:
    """
    A component to receive and process incidents from an Azure Storage Queue.
    Provides fault tolerance, retry backoff, and dead-letter/poison message handling.
    """
    def __init__(
        self,
        connection_string: Optional[str] = None,
        queue_name: Optional[str] = None,
        poison_queue_name: Optional[str] = None,
        max_retries: int = 5,
        base64_encode: bool = True
    ):
        """
        Initializes the AzureQueueReceiver with connection and queue details.
        
        Args:
            connection_string (str, optional): Connection string to the Azure Storage Account.
                Defaults to the 'AZURE_STORAGE_CONNECTION_STRING' environment variable.
            queue_name (str, optional): The name of the Azure Storage Queue.
                Defaults to the 'AZURE_INCIDENT_QUEUE_NAME' environment variable.
            poison_queue_name (str, optional): The name of the poison/dead-letter queue.
                Defaults to '{queue_name}-poison' or environment variable 'AZURE_POISON_QUEUE_NAME'.
            max_retries (int): Maximum number of retries before a message is marked as poison. Defaults to 5.
            base64_encode (bool): Whether queue messages are encoded in Base64. Defaults to True.
        """
        self.connection_string = connection_string or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        self.queue_name = queue_name or os.environ.get("AZURE_INCIDENT_QUEUE_NAME")
        self.poison_queue_name = poison_queue_name or os.environ.get("AZURE_POISON_QUEUE_NAME") or (
            f"{self.queue_name}-poison" if self.queue_name else None
        )
        self.max_retries = max_retries

        if not self.connection_string:
            raise AzureQueueReceiverError(
                "Azure Storage Connection String must be provided or set in the "
                "'AZURE_STORAGE_CONNECTION_STRING' environment variable."
            )
        if not self.queue_name:
            raise AzureQueueReceiverError(
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
            raise AzureQueueReceiverError(f"Failed to initialize main QueueClient: {str(e)}") from e

        self.poison_queue_client = None
        if self.poison_queue_name:
            try:
                self.poison_queue_client = QueueClient.from_connection_string(
                    conn_str=self.connection_string,
                    queue_name=self.poison_queue_name,
                    message_encode_policy=encode_policy,
                    message_decode_policy=decode_policy
                )
            except Exception as e:
                logger.warning(f"Failed to initialize poison QueueClient: {str(e)}")

    def create_queues_if_not_exists(self) -> None:
        """
        Creates both the main queue and the poison queue if they do not exist.
        """
        from azure.core.exceptions import ResourceExistsError
        for name, client in [("main", self.queue_client), ("poison", self.poison_queue_client)]:
            if not client:
                continue
            try:
                logger.info(f"Ensuring Azure Storage Queue ({name}) '{client.queue_name}' exists...")
                client.create_queue()
                logger.info(f"Queue '{client.queue_name}' created successfully.")
            except ResourceExistsError:
                logger.info(f"Queue '{client.queue_name}' already exists.")
            except AzureError as e:
                raise AzureQueueReceiverError(f"Failed to create Azure Queue '{client.queue_name}': {str(e)}") from e

    def parse_message_content(self, content: str) -> Union[Incident, Dict[str, Any], str]:
        """
        Helper method to parse message string content into an Incident model,
        a dictionary, or a fallback raw string.
        """
        if not content:
            return content

        # Try parsing as JSON first
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                # Only parse as Incident if there are keys matching Incident attributes or aliases
                incident_keys = {
                    "Resumen", "Notas", "Estado", "Resolucion", "Grupo_Propietario",
                    "Numero_Incidente", "FechaCreacionREG", "Vendor_Ticket_Number",
                    "CI", "CI_Vendor", "Reported_Date", "Grupo_Asignado"
                }
                lower_incident_keys = {k.lower() for k in incident_keys}
                has_matching_key = any(
                    k in incident_keys or k in lower_incident_keys for k in data.keys()
                )
                if has_matching_key:
                    try:
                        return Incident(**data)
                    except Exception:
                        return data
                return data
            return content
        except json.JSONDecodeError:
            return content

    def move_to_poison_queue(self, message: Any) -> None:
        """
        Moves a poison message to the poison queue and deletes it from the main queue.
        """
        if not self.poison_queue_client:
            logger.error("Poison queue client is not configured. Cannot move message.")
            return

        try:
            logger.warning(f"Moving message {message.id} (content: {message.content}) to poison queue...")
            # Send message to poison queue. Use default visibility timeout, no special TTL or settings needed
            self.poison_queue_client.send_message(message.content)
            logger.info(f"Successfully moved message {message.id} to poison queue.")
        except Exception as e:
            logger.error(f"Failed to send message to poison queue: {str(e)}")
            # Do not raise; we want to try to delete it from main queue anyway to avoid infinite poison loops,
            # or let it retry depending on application needs. Standard practice is raising to let the operator know.
            raise AzureQueueReceiverError(f"Poison queue delivery failed: {str(e)}") from e

    def receive_and_process_messages(
        self,
        handler: Callable[[Union[Incident, Dict[str, Any], str]], None],
        max_messages: int = 10,
        visibility_timeout: int = 30,
        retry_visibility_timeout: int = 10
    ) -> Dict[str, Any]:
        """
        Receives messages from the queue and processes them using the provided handler.
        Handles retries, poison message movement, and updates visibility timeout on failure.
        
        Args:
            handler: Callback function that accepts the parsed incident content and processes it.
            max_messages (int): Maximum number of messages to receive in this batch.
            visibility_timeout (int): Visibility timeout (seconds) for the received messages.
            retry_visibility_timeout (int): Custom visibility timeout (seconds) applied on retryable failure.
            
        Returns:
            dict: Summary metrics of the processing run (received, succeeded, failed, poisoned).
        """
        metrics = {
            "received": 0,
            "succeeded": 0,
            "failed": 0,
            "poisoned": 0
        }

        try:
            messages = list(self.queue_client.receive_messages(
                messages_per_page=max_messages,
                visibility_timeout=visibility_timeout
            ))
        except AzureError as e:
            raise AzureQueueReceiverError(f"Error receiving messages from queue: {str(e)}") from e

        if not messages:
            return metrics

        lock = threading.Lock()

        def process_message(msg: Any) -> None:
            # 1. Check if message has already exceeded maximum retry limit
            if msg.dequeue_count > self.max_retries:
                logger.warning(
                    f"Message {msg.id} has dequeue count {msg.dequeue_count} "
                    f"which exceeds max retries of {self.max_retries}. Treating as poison."
                )
                try:
                    self.move_to_poison_queue(msg)
                    self.queue_client.delete_message(msg)
                    with lock:
                        metrics["poisoned"] += 1
                except Exception as e:
                    logger.error(f"Failed to handle poison message: {str(e)}")
                    with lock:
                        metrics["failed"] += 1
                return

            # 2. Parse content
            parsed_content = self.parse_message_content(msg.content)

            # 3. Process message
            try:
                handler(parsed_content)
                # Successful processing -> Delete from queue
                self.queue_client.delete_message(msg)
                with lock:
                    metrics["succeeded"] += 1
                logger.info(f"Successfully processed and deleted message {msg.id}.")
            except TerminalMessageError as e:
                logger.error(f"Terminal error processing message {msg.id}: {str(e)}. Routing to poison queue.")
                try:
                    self.move_to_poison_queue(msg)
                    self.queue_client.delete_message(msg)
                    with lock:
                        metrics["poisoned"] += 1
                except Exception as ex:
                    logger.error(f"Failed to handle poison message after terminal error: {str(ex)}")
                    with lock:
                        metrics["failed"] += 1
            except Exception as e:
                logger.error(f"Error processing message {msg.id}: {str(e)}")
                with lock:
                    metrics["failed"] += 1

                # Check if this retry is the last allowed retry
                if msg.dequeue_count >= self.max_retries:
                    logger.warning(f"Message {msg.id} reached max retry limit on this failure. Routing to poison queue.")
                    try:
                        self.move_to_poison_queue(msg)
                        self.queue_client.delete_message(msg)
                        with lock:
                            metrics["poisoned"] += 1
                    except Exception as ex:
                        logger.error(f"Failed to move message to poison queue: {str(ex)}")
                else:
                    # Implement backoff / visibility timeout update to defer next retry
                    try:
                        # Visibility timeout backoff: standard or exponential based on dequeue count
                        backoff_seconds = retry_visibility_timeout * msg.dequeue_count
                        logger.info(f"Updating visibility timeout for message {msg.id} to {backoff_seconds} seconds.")
                        self.queue_client.update_message(
                            message=msg,
                            visibility_timeout=backoff_seconds
                        )
                    except Exception as ex:
                        logger.warning(f"Could not update visibility timeout for message {msg.id}: {str(ex)}")

        for message in messages:
            metrics["received"] += 1
            logger.info(f"Received message {message.id}. Dequeue count: {message.dequeue_count}")

        with ThreadPoolExecutor(max_workers=len(messages)) as executor:
            executor.map(process_message, messages)

        return metrics
