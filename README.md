# Tony: QA Test-Case Management Bot (Local Development)

Tony is a 24x7 QA test-case keeper for team chat. When you describe a test case in chat, Tony extracts structured fields, files it directly into your team's Excel workbook, saves attachments and screenshots, links Jira tickets, and informs you **exactly** where the test case lives (`Sheet "Auth", Row 2, cells A2:L2`).

Built for Cisco Webex with a fully isolated chat adapter layer, allowing Slack/Teams adapters to be added without touching core business logic or storage.

```text
@tony add test case: Login fails with valid password on Safari.
Steps: 1) open /login 2) enter valid user 3) click Sign in
Expected: dashboard opens. Actual: spinner forever.
module: Auth   priority: high   (screenshot attached)  https://jira.example.com/BUG-482
```
```text
Saved as TC-0001
File: testcases.xlsx | Sheet: Auth | Row 2 | Cells A2:L2
Screenshot: attachments/TC-0001_1.png   Link: BUG-482
```

---

## Commands & Syntax

| Command | Action |
|---|---|
| `@tony add test case: ...` (or multi-case: `Case 1: ... Case 2: ...`) | Extract fields, prompt for missing steps/expected results, detect duplicates, save to Excel, reply with exact cell coordinates |
| `@tony where is the Safari login test?` | Search the sheets, reply with best match + similarity score, or "No match" (never hallucinates) |
| `@tony show TC-0001` | Display full test case details |
| `@tony list high priority Auth cases` | Filtered list by priority, status, or module (e.g. `list open cases`, `list all`) |
| `@tony update TC-0001 status: passed` | Modify row in place; old values logged in `Audit` |
| `@tony delete TC-0001` then `yes` | Soft delete (row greyed out, Status=Deleted); `restore TC-0001` undoes it |
| `@tony help` | Display available commands |

**Recognized Labels (case-insensitive):** `title, module, priority, steps, expected, actual, environment, status, link` (plus Tamil aliases such as `படிகள்`). Text preceding the first label is captured as the title.

---

## Prerequisites & Requirements

- **Python Version**: Python 3.11, 3.12, or 3.13 (Windows, macOS, or Linux).
- **Webex Account**: Access to [developer.webex.com](https://developer.webex.com/) to create a Bot.
- **Local Webhook Tunnel (for Webex testing)**: [Cloudflare Quick Tunnel (`cloudflared`)](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) or [ngrok](https://ngrok.com/) to temporarily expose `http://localhost:8080`.

---

## Local Setup & Configuration

### 1. Create a Webex Bot
1. Navigate to [developer.webex.com](https://developer.webex.com/) and sign in.
2. Click your profile avatar $\rightarrow$ **My Webex Apps** $\rightarrow$ **Create a New App** $\rightarrow$ **Create a Bot**.
3. Enter:
   - **Bot name**: e.g. `Tony`
   - **Bot username**: e.g. `tony-test-bot@webex.bot`
   - **Description**: `QA Test Case Keeper Bot`
4. Click **Add Bot** and copy the **Bot access token**. Save it securely.
5. In your Webex client, create a test space and add your bot's username, or open a 1:1 chat with the bot. *(In group spaces, Tony responds only when @mentioned; in 1:1 direct chats, every message is processed).*

### 2. Environment Configuration
Copy the template configuration file:
```powershell
cp .env.example .env
```
Edit `.env` with your settings:

```env
# Mandatory for Webex integration:
WEBEX_BOT_TOKEN=your_webex_bot_access_token_here

# Recommended for signature verification:
WEBEX_WEBHOOK_SECRET=your_random_secret_string_here

# Optional: Set this to your temporary tunnel URL (e.g., from cloudflared or ngrok)
# When provided, Tony automatically registers and self-heals its webhook on startup
PUBLIC_URL=https://your-temporary-subdomain.trycloudflare.com

# Storage & Server
PORT=8080
DATA_DIR=./data
WORKBOOK_NAME=testcases.xlsx
TZ=Asia/Kolkata
LOG_LEVEL=INFO
```

### 3. Virtual Environment & Dependencies
In Windows PowerShell (from the project root `tony`):
```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install runtime dependencies
pip install -r requirements.txt

# (Optional) Install development/test dependencies
pip install -r requirements-dev.txt
```

---

## Webhook Connectivity (Local Development Tunnel)

Because Webex delivers events to public HTTPS endpoints:
1. Start your local tunnel pointing to Tony's local port (`8080`):
   ```powershell
   # Using Cloudflare Quick Tunnel:
   cloudflared tunnel --url http://localhost:8080

   # Or using ngrok:
   ngrok http 8080
   ```
2. Copy the generated HTTPS URL (e.g. `https://xxxx-xxxx.trycloudflare.com`).
3. Add the URL to `PUBLIC_URL` in `.env` (no trailing slash).
4. **Automatic Webhook Registration**: When Tony starts with `PUBLIC_URL` defined, `WebexAdapter.ensure_webhook()` automatically creates/updates the Webex webhook named `tony-messages` pointing to:
   ```text
   <PUBLIC_URL>/webex/webhook
   ```
   with resource `messages`, event `created`, and the HMAC secret.
5. If registering manually on [developer.webex.com/docs/api/v1/webhooks/create-a-webhook](https://developer.webex.com/docs/api/v1/webhooks/create-a-webhook):
   - **Name**: `tony-messages`
   - **Target URL**: `https://<your-tunnel-url>/webex/webhook`
   - **Resource**: `messages`
   - **Event**: `created`
   - **Secret**: `<WEBEX_WEBHOOK_SECRET>`

---

## Running Tony Locally

Start the FastAPI application:
```powershell
python -m bot.app
```

On startup, Tony will:
- Authenticate with Webex via `GET /people/me`.
- Display the verified Bot identity name and ID in console logs.
- Auto-register or verify the webhook if `PUBLIC_URL` is configured.
- Start the background keepalive thread (self-heals the webhook every 5 minutes).
- Serve incoming webhooks on `http://0.0.0.0:8080/webex/webhook`.

### Health Check
In another terminal, inspect Tony's operational health:
```powershell
curl http://localhost:8080/health
```
or via PowerShell:
```powershell
Invoke-RestMethod http://localhost:8080/health
```
Expected response:
```json
{
  "status": "ok",
  "uptime_s": 12,
  "workbook": "ok",
  "cases": 0,
  "write_queue": 0,
  "webhook": "active",
  "last_event_age_s": null
}
```

---

## Offline & Mock Testing

Tony includes a comprehensive test suite that runs without live Webex or internet connectivity:

```powershell
# Run the 28 end-to-end sample and edge-case scenarios (fake chat adapter)
python tests/run_samples.py

# Run the Webex adapter and webhook test suite against a mocked Webex API
python tests/test_webex_adapter.py
```

---

## Architecture & Data Flow

```text
Webex ──webhook──> bot/app.py (FastAPI: signature check, /health, ack in ms, worker pool)
                      │  bot/chat/webex.py  (ONLY Webex code: fetch message, download attachments, send)
                      ▼  IncomingMessage (platform-neutral)
                bot/core/service.py  (orchestrator: commands, follow-ups, duplicate detection, replies)
                  │            │             │
          parser.py    masking.py    textsim.py        (core/: pure functions, unit-testable)
                  │
                bot/storage/write_queue.py ──> bot/storage/excel_store.py ──> testcases.xlsx, attachments/, backups/
                bot/storage/state.py (SQLite: processed event IDs, pending draft conversations)
```

### Workbook Layout (`testcases.xlsx`)
- **Module Sheets** (e.g. `Auth`, `Billing`, `General`): Columns `TC ID | Title | Module | Priority | Steps | Expected | Actual | Environment | Links | Attachments | Status | Reporter / Date`.
- **`Index`**: Automatically synchronized mapping of `TC ID -> Sheet, Row, Title` with direct clickable Excel hyperlink jumping to `#'<sheet>'!A<row>`.
- **`Audit`**: Immutable event history tracking `Timestamp, User, Action (ADD/UPDATE/DELETE/RESTORE), TC ID, Field, Old value, New value, Source message ID`.

---

## Guardrails & Reliability

- **Deterministic Parsing**: Never hallucinates or invents missing steps or expected outcomes. Asks follow-up questions instead of writing incomplete records.
- **Sensitive Data Masking**: Automatically masks passwords, bearer tokens, API keys, Aadhaar numbers, and credit card numbers *before* parsing, saving, or logging.
- **Excel Concurrency & Open File Protection**: Uses in-process locking and cross-process `filelock`. If an engineer has `testcases.xlsx` open in Excel, writes are queued in memory, the user is notified, and Tony retries every 10 seconds until unlocked.
- **Automated Backups**: Creates a timestamped backup before every write in `data/backups/` (keeps latest 20).
- **Idempotency**: Protects against duplicate webhook deliveries at two tiers: SQLite event claims and `<message_id>#<n>` checks in the `Audit` sheet.

---

## Project Structure

```text
tony/
├── bot/
│   ├── __init__.py
│   ├── app.py              # FastAPI app, /health, /webex/webhook, keepalive loop
│   ├── config.py           # Configuration loaded from environment (.env)
│   ├── models.py           # Platform-neutral data models
│   ├── chat/               # Platform adapter layer (Webex, Fake)
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── webex.py
│   │   └── fake.py
│   ├── core/               # Pure business logic (no I/O to Webex or Excel)
│   │   ├── __init__.py
│   │   ├── service.py
│   │   ├── parser.py
│   │   ├── masking.py
│   │   └── textsim.py
│   └── storage/            # Concurrency queue, Excel store & SQLite state
│       ├── __init__.py
│       ├── excel_store.py
│       ├── write_queue.py
│       └── state.py
├── tests/
│   ├── run_samples.py      # Offline sample & edge-case replay test suite
│   └── test_webex_adapter.py # Real Webex adapter & webhook test suite
├── docs/
│   └── DEMO_SCRIPT.md      # Demo script walkthrough
├── sample/                 # Sample workbook, attachments and transcripts
├── data/
│   └── .gitkeep            # Runtime storage directory (workbook, sqlite, attachments)
├── .env.example            # Environment variables template
├── .gitignore              # Git ignore rules (.env, data/*, __pycache__)
├── requirements.txt        # Runtime dependencies
├── requirements-dev.txt    # Testing dependencies
├── README.md               # Project documentation
└── NOTES.md                # Design decisions, assumptions, and limits
```

---

## Troubleshooting

- **`RuntimeError: WEBEX_BOT_TOKEN is not set`**: Create `.env` in the root folder and populate `WEBEX_BOT_TOKEN`.
- **`401 Unauthorized` on startup**: Your Webex bot token is expired or invalid. Generate a new token on [developer.webex.com](https://developer.webex.com/).
- **`401 Unauthorized` on `/webex/webhook`**: The incoming Webex webhook signature does not match `WEBEX_WEBHOOK_SECRET`. Ensure the secret in `.env` matches the secret configured on the Webex webhook.
- **Bot does not respond in group space**: In group spaces, Webex only notifies bots when explicitly @mentioned (e.g. `@Tony add test case:...`). In 1:1 direct messages, no mention is required.
- **Workbook Locked / Retrying**: Microsoft Excel locks open files against external atomic replace. Close the file in Excel, and Tony will commit the queued write.
