# Tony: QA Test-Case Management Bot

Tony is an automated, 24x7 QA test-case keeper built for Cisco Webex. When engineers or testers describe a bug or test scenario in chat, Tony extracts structured fields, stores the case into the team's shared Excel workbook, archives attachments and screenshots, links Jira tickets, and reports back the **exact sheet and cell coordinates** (e.g. `Sheet "Auth", Row 2, cells A2:L2`).

---

## Problem Statement & Purpose

In fast-paced engineering teams, test scenarios and bug repro steps shared in chat channels are easily lost in chat history or copy-pasted haphazardly into messy spreadsheets. 

**Tony solves this by serving as a zero-friction bridge between team chat and spreadsheet persistence:**
- **No manual entry**: Log test cases directly from Webex using natural chat messages.
- **Accurate localization**: Every saved case is pinned to an exact row and range in Excel with an auto-rebuilt Index sheet.
- **Data integrity**: Protects against formula injection, race conditions, file corruption, and Excel file-locking conflicts.
- **Audit trail**: Maintains an immutable log of every addition, update, soft-delete, and restoration.
- **Strict safety guardrails**: Masks credentials and tokens before persistence, and never hallucinates or invents missing test steps.

---

## Core Features

- 📝 **Structured Case Extraction**: Intelligently captures Title, Steps, Expected, Actual, Module, Priority, Environment, and URLs.
- 🛡️ **Zero-Hallucination Guardrail**: Never guesses or invents missing reproduction steps or expected outcomes; pauses and prompts the user for clarification.
- 🔐 **Pre-Parse Sensitive Data Masking**: Automatically detects and masks passwords, bearer tokens, JWTs, vendor API keys, credit cards (Luhn-verified), and Aadhaar numbers *before* parsing, logging, or saving.
- 🔍 **Fuzzy Natural Language Search**: Answers questions like `@Tony where is the Safari login test?` using typo-tolerant token matching and sequence similarity without calling an external LLM.
- ⚠️ **Duplicate Detection**: Computes similarity scores against existing cases in the sheet to prevent redundant test filings.
- 📊 **Resilient Excel Storage Engine**:
  - One sheet per module (`Auth`, `Billing`, `UI`, `General`).
  - Auto-synchronized `Index` sheet with direct clickable Excel hyperlinks (`#'Auth'!A2`).
  - Immutable `Audit` log tracking timestamps, users, actions (`ADD`, `UPDATE`, `DELETE`, `RESTORE`), old values, and new values.
- 🔒 **File-Lock & Concurrency Protection**: Multi-tier concurrency model with in-process locks, cross-process `filelock`, and a single-worker FIFO `WriteQueue`. If an engineer has the Excel file open, Tony queues the write, informs the user, and retries automatically every 10 seconds.
- 🔄 **Safe Mutations**: Non-destructive soft deletes (greyed out with strikethrough in Excel) with one-click restoration. IDs are never recycled (`TC-0001`, `TC-0002`).
- 📦 **Automated Rolling Backups**: Timestamped copy saved in `data/backups/` before every write (keeps the latest 20); auto-heals corrupted workbooks from the newest backup.

---

## Technology Stack

- **Language**: Python 3.11+ (tested on Python 3.12 and 3.13)
- **Web Framework**: [FastAPI](https://fastapi.tiangolo.com/) with asynchronous lifespan handlers
- **ASGI Server**: [Uvicorn](https://www.uvicorn.org/)
- **Spreadsheet Engine**: [openpyxl](https://openpyxl.readthedocs.io/)
- **Concurrency & Locking**: Standard library `threading`, `queue`, and [filelock](https://py-filelock.readthedocs.io/)
- **State & Idempotency**: [SQLite3](https://docs.python.org/3/library/sqlite3.html) (WAL mode)
- **Containerization**: [Docker](https://www.docker.com/) & Docker Compose
- **Platform APIs**: Cisco Webex REST APIs (v1) & Webhooks with HMAC-SHA1 signature verification

---

## Architecture Diagram

```text
Cisco Webex
    │
    ▼ (HTTPS Webhook Event)
app/main.py (FastAPI: HMAC-SHA1 validation, 200 OK immediate ack, async thread pool)
    │
    ├──> app/integrations/webex.py (Fetch full message, download attachments, post responses)
    │        │
    │        ▼ (Platform-neutral IncomingMessage)
    └──> app/services/bot_service.py (Orchestrator: commands, pending conversations, replies)
             │
             ├──> app/services/security.py (Regex & Luhn credential masking BEFORE parsing)
             │
             ├──> app/services/command_parser.py (Deterministic command & test-case parsing)
             │
             ├──> app/services/similarity.py (Tokenization & similarity for search / duplicates)
             │
             └──> app/storage/queue.py (Serialised FIFO write worker thread + lock retries)
                      │
                      ▼
                  app/storage/excel.py (Atomic read-modify-write -> testcases.xlsx, backups)
                      │
                  app/storage/state.py (SQLite: event idempotency & pending draft conversations)
```

---

## Simplified Project Structure

```text
tony/
├── app/
│   ├── __init__.py           # Package marker
│   ├── main.py               # FastAPI application, webhook listener, and healthcheck
│   ├── config.py             # Central environment and settings loader (.env)
│   ├── models.py             # Platform-neutral data models (IncomingMessage, Draft, CaseRecord)
│   │
│   ├── services/
│   │   ├── __init__.py       # Service exports
│   │   ├── bot_service.py    # Core business logic and command execution flow
│   │   ├── command_parser.py # Rule-based command and field extraction
│   │   ├── security.py       # Sensitive credential and personal data masking
│   │   └── similarity.py     # Tokenization, fuzzy similarity, and duplicate scoring
│   │
│   ├── integrations/
│   │   ├── __init__.py       # Integration exports
│   │   ├── base.py           # Abstract ChatAdapter contract
│   │   ├── webex.py          # Production Cisco Webex REST & Webhook adapter
│   │   └── fake.py           # In-memory mock adapter for offline testing
│   │
│   └── storage/
│       ├── __init__.py       # Storage exports
│       ├── excel.py          # ExcelStore: atomic transactions, Index, and Audit
│       ├── queue.py          # WriteQueue: FIFO worker thread with lock retry
│       └── state.py          # SQLite engine for idempotency and pending drafts
│
├── tests/
│   ├── __init__.py           # Tests package marker
│   ├── test_bot.py           # Offline functional test suite (12 samples + 13 edge cases)
│   └── test_webex.py         # Mocked Webex API adapter and webhook test suite
│
├── docs/
│   └── SETUP.md              # Detailed local setup, Webex bot creation, and Docker guide
│
├── sample/                   # Sample workbook, attachments, and transcripts
├── data/
│   └── .gitkeep              # Persistent storage mount (testcases.xlsx, state.db, backups)
│
├── Dockerfile                # Production multi-layer Docker image definition
├── docker-compose.yml        # Docker Compose configuration with volume persistence
├── .dockerignore             # Excludes secrets, virtual environments, and local data
├── .env.example              # Environment variables template with explanations
├── .gitignore                # Git ignore rules (.env, data/*, __pycache__)
├── requirements.txt          # Production runtime dependencies
└── README.md                 # Primary project documentation
```

---

## Request Flow & Interview Explainability

When explaining Tony's technical architecture in an interview, trace an incoming message through this 8-step pipeline:

1. **Webex Message Event**: A user types a command (e.g. `@Tony add test case:...`) in a direct chat or group space. Cisco Webex dispatches an HTTP POST webhook containing event metadata.
2. **FastAPI Webhook Listener (`app/main.py`)**: 
   - Receives the raw payload and verifies the HMAC-SHA1 signature from the `X-Spark-Signature` header against `WEBEX_WEBHOOK_SECRET`.
   - Returns an immediate `{"ok": True}` HTTP 200 response to Webex (within milliseconds) to prevent webhook timeouts or retries.
   - Hands off the event payload to an asynchronous `ThreadPoolExecutor` worker.
3. **Webex Integration Adapter (`app/integrations/webex.py`)**:
   - Webex webhooks deliberately omit message text for security; the adapter makes an authenticated `GET /messages/{id}` call using the bot token.
   - Discards loopback messages generated by the bot itself (`personId == bot.id`).
   - Downloads any attached image/file streams and normalizes the payload into a platform-neutral `IncomingMessage` object.
4. **Security & Data Sanitization (`app/services/security.py`)**:
   - Before parsing or storage, the raw text is scanned for passwords, tokens, API keys, and Luhn-valid credit card numbers, masking them in memory.
5. **Deterministic Command Parser (`app/services/command_parser.py`)**:
   - Strips the `@Tony` mention and matches the command pattern (`add`, `where`, `show`, `list`, `update`, `delete`, `restore`).
   - Extracts structured labels (`Steps:`, `Expected:`, `Actual:`, `module:`, `priority:`).
6. **Bot Orchestration Service (`app/services/bot_service.py`)**:
   - Claims the `event_id` atomically in SQLite (`app/storage/state.py`) to guarantee idempotency.
   - **Guardrail Check**: If reproduction steps or expected results are missing, Tony halts the flow, stages the draft in SQLite, and prompts the user for clarification.
   - **Duplicate Check**: Computes fuzzy similarity against existing records via `app/services/similarity.py`. If similarity exceeds the threshold, prompts the user before writing.
7. **Thread-Safe Excel Persistence (`app/storage/queue.py` & `app/storage/excel.py`)**:
   - Mutating jobs are enqueued onto a dedicated background FIFO writer thread.
   - Acquires thread locks and cross-process file locks.
   - If the spreadsheet is open in Microsoft Excel, catches the lock, notifies the user that the write is queued, and retries every 10 seconds.
   - Allocates the next sequential ID (`TC-XXXX`), appends the row to the corresponding module sheet, registers the audit record in `Audit`, rebuilds the `Index` sheet with clickable hyperlinks, saves a timestamped backup, and performs an atomic file replacement via `os.replace`.
8. **Webex Reply Delivery (`app/integrations/webex.py`)**:
   - Tony formats the confirmation message with exact coordinates (`Saved as TC-0001 | Sheet: Auth | Row 2 | Cells A2:L2`).
   - Posts the reply back to the Webex room via Webex REST API (`POST /messages`).

---

## Commands & Syntax

| User Command | Tony Action |
|---|---|
| `@Tony add test case: <title>. Steps: 1) ... Expected: ... Actual: ... module: Auth priority: high` | Extracts fields, validates required steps, detects duplicates, files in Excel, and replies with coordinates |
| `@Tony where is the Safari login test?` | Performs fuzzy text search across all module sheets and returns best match + location |
| `@Tony show TC-0001` | Displays full test case details card |
| `@Tony list high priority Auth cases` | Returns filtered list by priority, module, or status |
| `@Tony update TC-0001 status: passed` | Updates row in place and records old/new values in the `Audit` sheet |
| `@Tony delete TC-0001` | Requests confirmation, then marks row as `Deleted` (soft delete with strikethrough) |
| `@Tony restore TC-0001` | Restores soft-deleted case back to its previous status |
| `@Tony help` | Displays the command cheat sheet |

---

## Quickstart & Local Execution

### 1. Configure `.env`
Create `.env` from `.env.example`:
```powershell
cp .env.example .env
```
Fill in your `WEBEX_BOT_TOKEN` and `WEBEX_WEBHOOK_SECRET` (generate secret via `python -c "import secrets; print(secrets.token_hex(32))"`).

### 2. Run Locally
```powershell
# Install dependencies
pip install -r requirements.txt

# Start Tony
python -m app.main
```
The server starts on `http://0.0.0.0:8080`. Verify health:
```powershell
curl http://localhost:8080/health
```

### 3. Expose via Temporary Tunnel (for Live Webex Testing)
Run in a separate terminal:
```powershell
ngrok http 8080
# or: cloudflared tunnel --url http://localhost:8080
```
Add the forwarding URL to `PUBLIC_URL` in `.env` and restart Tony. Tony automatically registers and maintains its Webex webhook.

---

## Docker Deployment

### 1. Build & Run
```powershell
docker compose up -d --build
```

### 2. View Logs & Container Status
```powershell
docker compose ps
docker compose logs -f
```

### 3. Stop Container
```powershell
docker compose down
```

*All Excel sheets, SQLite databases, attachments, and backups persist in the `./data` folder across container rebuilds.*

---

## Testing & Verification

Tony includes two comprehensive, deterministic test suites:

### Functional & Scenario Test Suite (Offline)
Replays 12 end-to-end sample scenarios and 13 edge cases using an isolated in-memory test chat adapter:
```powershell
python tests/test_bot.py
```
*(Result: 28/28 tests passed)*.

### Webex Adapter & Webhook Mock Suite
Verifies signature validation, Webex message fetching, attachment downloading, and rate-limit retries against a mocked Webex API:
```powershell
python tests/test_webex.py
```
*(Result: 8/8 tests passed)*.

---

## Detailed Documentation

For step-by-step setup walkthroughs, Webex bot creation instructions, and troubleshooting, see [docs/SETUP.md](docs/SETUP.md).
