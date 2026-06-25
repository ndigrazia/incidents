import os
import sys
import time
import signal
import logging
from typing import Union, Dict, Any
from dotenv import load_dotenv

from queue.azure_queue_receiver import AzureQueueReceiver
from queue.azure_queue_sender import AzureQueueSender
from loader.utils import Incident
from handler import NotesSummarizer

# Configure logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)

logger = logging.getLogger("main_receiver")

load_dotenv()

# Initialize summarizer
summarizer = NotesSummarizer()
sender = None


def get_sender() -> AzureQueueSender:
    """Get the initialized AzureQueueSender instance."""
    global sender
    if sender is None:
        queue_name = os.environ.get("AZURE_SUMMARIES_QUEUE_NAME") or os.environ.get("SUMMARIES_QUEUE_NAME") or "incidents-summaries"
        sender = AzureQueueSender(queue_name=queue_name)
    return sender

# Global flag for graceful shutdown
running = True


def handle_shutdown(signum, frame):
    global running
    logger.info(f"Received signal {signum}. Shutting down gracefully...")
    running = False


def process_incident(incident: Union[Incident, Dict[str, Any], str]) -> None:
    """
    Example handler callback to process the received incident.
    Summarizes the notes and sends the summary to the "incidents-summaries" queue.
    If no summary is available, sends "No comment".
    """
    logger.info("--- Processing Received Incident ---")
    summary = None

    if isinstance(incident, Incident):
        logger.info(f"Model Incident ID/Numero: {incident.numero_incidente}")
        logger.info(f"Status: {incident.estado}")
        logger.info("Summarizing Incident Notes...")
        try:
            summary = summarizer.summarize(incident.notes)
            logger.info(f"Summary of Notes: {summary}")
        except Exception as e:
            logger.error(f"Error summarizing notes: {str(e)}")
    elif isinstance(incident, dict):
        logger.info(f"Dict Incident: {incident}")
        notes = incident.get("notes") or incident.get("Notas")
        if notes:
            logger.info("Summarizing Incident Notes from dict...")
            try:
                summary = summarizer.summarize(notes)
                logger.info(f"Summary of Notes: {summary}")
            except Exception as e:
                logger.error(f"Error summarizing notes from dict: {str(e)}")
    else:
        logger.info(f"Raw string incident content: {incident}")

    # Determine the incident ID
    incident_id = None
    if isinstance(incident, Incident):
        incident_id = incident.numero_incidente
    elif isinstance(incident, dict):
        incident_id = (
            incident.get("Numero_Incidente")
            or incident.get("numero_incidente")
            or incident.get("id")
        )

    # Determine what summary message to send
    if not summary or not summary.strip() or summary == "No notes provided to summarize.":
        summary_to_send = "No comment"
    else:
        summary_to_send = summary

    # Prefix with Incident ID if available
    if incident_id:
        message_to_send = f"Incident {incident_id}: {summary_to_send}"
    else:
        message_to_send = summary_to_send

    try:
        sender_instance = get_sender()
        logger.info(f"Sending summary to queue '{sender_instance.queue_name}'...")
        sender_instance.send_incident(message_to_send)
    except Exception as e:
        logger.error(f"Failed to send summary to queue: {str(e)}")
        raise e

    logger.info("------------------------------------")


def main():
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Load configuration
    max_retries = int(os.environ.get("RECEIVER_MAX_RETRIES", 5))
    poll_interval = float(os.environ.get("RECEIVER_POLL_INTERVAL", 2.0))
    max_messages = int(os.environ.get("RECEIVER_MAX_MESSAGES", 10))
    visibility_timeout = int(os.environ.get("RECEIVER_VISIBILITY_TIMEOUT", 30))
    retry_visibility_timeout = int(os.environ.get("RECEIVER_RETRY_VISIBILITY_TIMEOUT", 10))

    logger.info("Initializing Azure Incident Queue Receiver & Sender...")
    try:
        receiver = AzureQueueReceiver(max_retries=max_retries)
        receiver.create_queues_if_not_exists()

        # Initialize the sender and create the queue if not exists
        global sender
        sender = get_sender()
        sender.create_queue_if_not_exists()
    except Exception as e:
        logger.critical(f"Failed to initialize receiver or sender: {str(e)}")
        sys.exit(1)

    logger.info(f"Queue receiver is running. Polling queue '{receiver.queue_name}' every {poll_interval} seconds. Press Ctrl+C to exit.")

    while running:
        try:
            metrics = receiver.receive_and_process_messages(
                handler=process_incident,
                max_messages=max_messages,
                visibility_timeout=visibility_timeout,
                retry_visibility_timeout=retry_visibility_timeout
            )
            if metrics["received"] > 0:
                logger.info(f"Processed batch metrics: {metrics}")
        except Exception as e:
            logger.error(f"Error during message receiving and processing cycle: {str(e)}")
        
        # Sleep until the next polling cycle
        if running:
            time.sleep(poll_interval)

    logger.info("Queue receiver has shutdown successfully.")


if __name__ == "__main__":
    main()
