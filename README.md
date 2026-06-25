# BMC Remedy to Azure Storage Queue Integration & AI Incident Summarizer

A high-performance, production-ready Python service suite designed to pull incident records from the BMC Remedy `TASA_Query_Incident` SOAP web service, validate and model them using Pydantic, dispatch them to an Azure Storage Queue, and automatically process and summarize their notes using a LangChain-powered LLM (supporting both Google Gemini and LiteLLM Proxy) before pushing them to a downstream Summaries Queue.

---

## Architecture Overview

The system operates as an asynchronous, decoupled publisher-subscriber architecture with an integrated GenAI summarization pipeline.

```
+------------------+             +------------------------+             +----------------------------+
|                  |             |                        |             |                            |
|    BMC Remedy    | <=========> |   SOAP Client Loader   | ==========> | Azure Storage Queue (Main) |
|   SOAP Service   |    SOAP     |      (Publisher)       |   HTTPS     |      [loader_incidents.py] |
|                  |   Request   |  [loader/soap_client]  |             +----------------------------+
| +------------------+             +------------------------+                           ||
                                                                                        || (Pull Messages)
                                                                                        \/
+------------------+             +------------------------+             +----------------------------+
|  Summaries Queue |             |  Queue Receiver Daemon |             |                            |
|    "incidents-   | <========== |       (Subscriber)     | <========== | Azure Storage Queue Poison |
|    summaries"    |   Publish   | [receiver_incidents.py]|   HTTPS     |     (Dead Letter Queue)    |
+------------------+             +------------------------+             +----------------------------+
                                             ||
                                             || (Summarize via LangChain LCEL)
                                             \/
                                 +------------------------+
                                 |   AI Notes Summarizer  |
                                 |  [handler/summarizer]  |
                                 +------------------------+
```

### Core Flow Components
1. **Publisher Client (`loader_incidents.py` -> `loader/soap_client.py`)**:
   - Fetches incidents matching a timestamp-based query window.
   - Built-in session state management via tracking files (`conf/query_time.txt` & `out/query_times_history.txt`).
   - Ensures no gaps or overlaps in the fetched timespans.
2. **Azure Queue Broker (`queue/azure_queue_sender.py`)**:
   - Enforces Base64 text encoding to strictly conform with Azure Storage Queue requirements.
   - Idempotently creates queues on startup if they do not exist.
3. **Receiver Daemon (`receiver_incidents.py` -> `queue/azure_queue_receiver.py`)**:
   - Polls messages from Azure Storage Queue continuously.
   - Directs failed message retries using visibility timeouts and automatically redirects toxic or repeatedly failing messages to a secondary Poison Queue (`-poison`) to prevent main queue blockages.
   - Calls the summarizer component on each processed incident.
   - Formats and sends the output to a dedicated Summaries Queue.
   - Handles termination signals gracefully to prevent dirty state shutdowns.
4. **AI Notes Summarizer (`handler/summarizer.py`)**:
   - Built on LangChain Expressive Language (LCEL) supporting both Google Gemini and LiteLLM Proxy.
   - Condenses complex incident notes into a concise, actionable summary.
   - Performs strict environment variable and parameter validations during initialization.

---

## Key Features

- **Multi-Provider LLM Support**: Dynamically switch between Google Gemini (`gemini`) and LiteLLM Proxy (`litellm`) using environment variables or initialization parameters.
- **Strict Validation**: Performs comprehensive config validation upon initialization, raising meaningful exceptions if any required API credentials or configurations are missing.
- **Decoupled AI Pipeline**: Automatically processes incidents as they are received. Incident notes are sent to the selected LLM, and the summary is forwarded to the downstream queue.
- **Incident ID Prefixing**: All messages published to the summaries queue are prefixed with `Incident <incident_id>: <summary>` (or `No comment` if no notes or summary were available) to guarantee clear traceability.
- **Configurable Downstream Queue**: The target queue for summaries defaults to `"incidents-summaries"` but is completely customizable using environment variables.
- **Robust Exception and Retry Strategy**: Built-in exponential backoff using `tenacity` wraps outgoing HTTP requests, shielding the publisher from transient SOAP-endpoint network timeouts.
- **XML Injection Defenses**: Dynamically generates SOAP envelopes while strictly escaping all inputs (qualification queries, authentication credentials) to eliminate SOAP/XML injection vulnerabilities.
- **State Integrity**: To prevent any possibility of message loss, state files tracking the last successful query window are only committed and written *after* messages are successfully dispatched to Azure Storage.
- **Poison Queue & Backoff Routing**: Protects message consumers. A message is retried up to a configurable number of times. If processing continues to throw exceptions, it is cleanly routed to a Poison Queue.
- **Graceful Shutdown**: The receiver daemon intercepts `SIGINT` (Ctrl+C) and `SIGTERM`, allowing active message batches to complete execution before shutting down safely.

---

## Directory Structure

*Note: This structure represents the primary Remedy-to-Azure service integration modules.*

```
├── loader/
│   ├── __init__.py
│   ├── soap_client.py           # SOAP Query logic, tenacity retries, and timestamp state orchestration
│   └── utils/
│       ├── __init__.py
│       └── models.py            # Pydantic schema mappings for Remedy Incidents and raw SOAP responses
├── handler/
│   ├── __init__.py
│   └── summarizer.py            # LangChain Gemini & LiteLLM Proxy summarizing component
├── queue/
│   ├── __init__.py
│   ├── azure_queue_sender.py    # Azure Queue Publisher (base64-encoded JSON/text dispatcher)
│   └── azure_queue_receiver.py  # Azure Queue Consumer (poison queue routing, retry policies)
├── tests/                       # Complete pytest suite isolating SOAP, Receiver, Sender, and Summarizer
│   ├── test_azure_queue_receiver.py
│   ├── test_azure_queue_sender.py
│   ├── test_receiver_incidents.py
│   ├── test_soap_client.py
│   └── test_summarizer.py
├── loader_incidents.py          # Application entrypoint to run the Remedy SOAP-to-Azure Publisher
├── receiver_incidents.py        # Application entrypoint to run the Azure Queue Receiver Daemon & AI pipeline
├── conf/
│   └── query_time.txt           # Current state file tracking last query window start time
```

---

## Configuration

Environment variables can be set directly in a `.env` file located in the root directory.

```env
# ==========================================
# Remedy SOAP Service Settings
# ==========================================
REMEDY_SOAP_URL=https://remedyaverias20.movistar.com.ar:8443/arsys/services/ARService?server=aparrdyresp101&webService=TASA_Query_Incident
REMEDY_USERNAME=your_username
REMEDY_PASSWORD=your_password
REMEDY_TIMEOUT=100
REMEDY_TIME_FILE=conf/query_time.txt
REMEDY_HISTORY_FILE=out/query_times_history.txt

# ==========================================
# Azure Storage Queue Settings
# ==========================================
AZURE_STORAGE_CONNECTION_STRING=your_azure_storage_connection_string
AZURE_INCIDENT_QUEUE_NAME=your_target_queue_name
AZURE_POISON_QUEUE_NAME=your_target_queue_name-poison

# ==========================================
# AI Summaries Downstream Queue Settings
# ==========================================
AZURE_SUMMARIES_QUEUE_NAME=incidents-summaries

# ==========================================
# LLM Provider Selection
# ==========================================
LLM_PROVIDER=gemini # Option: gemini, litellm

# ==========================================
# Gemini AI Configuration (if provider=gemini)
# ==========================================
GEMINI_MODEL_NAME=gemini-2.5-flash
GOOGLE_API_KEY=your_gemini_api_key

# ==========================================
# LiteLLM Proxy Configuration (if provider=litellm)
# ==========================================
LITELLM_API_BASE=https://omni-gateway-test.az.tcoretrack.com:9443/llm-gateway
LITELLM_API_KEY=your_litellm_api_key
LITELLM_MODEL_NAME=openai/gemini/llm
LITELLM_CLIENT_ID=your_litellm_client_id
LITELLM_CLIENT_SECRET=your_litellm_client_secret

# ==========================================
# Receiver Daemon Settings
# ==========================================
RECEIVER_MAX_RETRIES=5                   # Max processing attempts before a message is marked as poison
RECEIVER_POLL_INTERVAL=2.0               # Frequency (seconds) to poll Azure Storage Queue
RECEIVER_MAX_MESSAGES=10                 # Max messages pulled in a single batch
RECEIVER_VISIBILITY_TIMEOUT=30           # Seconds before a pulled message becomes visible again to other consumers
RECEIVER_RETRY_VISIBILITY_TIMEOUT=10     # Visibility window (seconds) during retry attempts
```

---

## Installation & Environment Management

This project uses [uv](https://github.com/astral-sh/uv) to manage its virtual environment and dependencies rapidly and securely.

### Prerequisites
- Python 3.12+
- `uv` installed on your machine

### Setup
Initialize and synchronize dependencies into a localized virtual environment:

```bash
uv sync
```

This will automatically create a `.venv` directory and download all required packages defined in `pyproject.toml`.

---

## Running the Application

### 1. Execute the Remedy to Azure Storage Publisher
To execute a one-time synchronization query that fetches incidents from BMC Remedy and publishes them to the Azure Queue:

```bash
uv run python loader_incidents.py
```

### 2. Execute the Azure Queue Consumer Daemon & AI Pipeline
To run the background processor that continuously polls the Azure Storage Queue, generates incident notes summaries using the selected LLM, and dispatches them to the summaries queue:

```bash
uv run python receiver_incidents.py
```
*Press `Ctrl + C` at any point to stop. The daemon will intercept the signal and shutdown gracefully without interrupting any currently running messages.*

---

## Test Suite Execution

The codebase contains a comprehensive unit testing architecture with **57 test cases** covering negative scenarios, network timeouts, serialization, state management, LLM provider selection and validation, custom queue environment naming, and retry semantics. All testing leverages mocks to guarantee no outbound calls are executed.

To run the complete test suite:

```bash
PYTHONPATH=. uv run pytest -v
```

### Key Test Categories Covered:
- **LLM Provider & Configuration Validation**: Verifies that the summarizer raises correct exceptions when provider parameters are missing, and handles both `gemini` and `litellm` selection properly.
- **AI Summary Logic**: Verifies correct summarization outputs, fallback behavior on LLM failure, and empty/whitespace handling.
- **Incident ID Prefix Assertions**: Confirms that queue messages correctly bundle Incident IDs in both Pydantic model formats and raw JSON dictionaries.
- **XML Parsing Integrity**: Verifies security against SOAP-injection exploits, empty XML envelopes, and missing elements.
- **Tenacity Resilience Checks**: Guarantees requests retry exactly the correct number of times upon networking errors (patched to execute without real delays).
- **Transactional State Commit**: Assures `conf/query_time.txt` updates are executed purely when messages are fully and successfully queued downstream.
- **Azure Storage Queue Robustness**: Confirms that lock contention, queue creation collisions, Base64 transformations, and poison-message redirection work as intended.
