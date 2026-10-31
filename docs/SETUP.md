# Tony QA Bot: Detailed Setup & Operations Guide

This guide covers step-by-step instructions for running Tony locally, establishing webhook tunnels, containerizing with Docker, executing functional verification tests, and troubleshooting.

---

## 1. System & Runtime Requirements

- **Python**: Version 3.11, 3.12, or 3.13.
- **Operating System**: Windows, macOS, or Linux.
- **Docker** (optional): Docker Desktop or Docker Engine with Docker Compose v2+.
- **Tunnel Utility** (for local development Webex testing): [ngrok](https://ngrok.com/) or [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

---

## 2. Webex Bot Setup

1. Log in to the [Cisco Webex Developer Portal](https://developer.webex.com/).
2. Click your avatar $\rightarrow$ **My Webex Apps** $\rightarrow$ **Create a New App** $\rightarrow$ **Create a Bot**.
3. Fill in:
   - **Bot name**: `Tony` (or your preferred name)
   - **Bot username**: `your-bot-handle@webex.bot` (must be unique on Webex)
   - **Description**: `QA Test Case Management Bot`
4. Click **Add Bot** and copy the **Bot access token**. Save it securely.
5. In your Cisco Webex client, create a test space or open a 1:1 chat with `your-bot-handle@webex.bot`.

---

## 3. Environment Configuration (`.env`)

In the project root, ensure `.env` is created from [`.env.example`](file:///c:/Users/ACER/Downloads/tony/tony/.env.example):

```powershell
cp .env.example .env
```

### Required & Configurable Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `WEBEX_BOT_TOKEN` | **Yes** | — | Bot access token from Webex Developer Portal. |
| `WEBEX_WEBHOOK_SECRET` | **Yes** | — | Cryptographic secret for verifying Webex `X-Spark-Signature` HMAC headers. |
| `PUBLIC_URL` | Optional | `""` | Public HTTPS tunnel URL (e.g. `https://xxxx.ngrok-free.app`). When set, Tony auto-provisions and self-heals its Webex webhook. |
| `PORT` | Optional | `8080` | Local HTTP port for FastAPI and Uvicorn. |
| `DATA_DIR` | Optional | `./data` | Directory where `testcases.xlsx`, `state.db`, `attachments/`, and `backups/` reside. |
| `WORKBOOK_NAME` | Optional | `testcases.xlsx` | Name of the primary Excel workbook. |
| `TZ` | Optional | `Asia/Kolkata` | Timezone used for Excel timestamps and audit logs. |
| `LOG_LEVEL` | Optional | `INFO` | Console logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `DUPLICATE_THRESHOLD`| Optional | `0.60` | Similarity score threshold to trigger duplicate case warnings. |
| `SEARCH_THRESHOLD` | Optional | `0.50` | Coverage threshold for natural language search matches. |
| `LOCK_RETRY_SECONDS` | Optional | `10` | Frequency to re-attempt writing if Excel has the file locked. |
| `LOCK_MAX_WAIT_SECONDS`| Optional | `900` | Maximum queue timeout (15 min) for locked workbooks before discarding. |
| `PENDING_TTL_SECONDS`| Optional | `900` | Expiration window (15 min) for follow-up questions and delete confirmations. |
| `MAX_ATTACHMENT_MB` | Optional | `10` | Attachment file size ceiling in megabytes. |
| `MASK_EMAILS` | Optional | `false` | When `true`, masks email addresses in addition to passwords and tokens. |
| `HEALTHCHECK_PING_URL`| Optional | `""` | Optional URL pinged every 5 min (dead-man's-switch monitoring). |

### Generating a Webhook Secret
Run this in PowerShell or bash:
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste the generated 64-character hex string into `WEBEX_WEBHOOK_SECRET` in `.env`.

---

## 4. Local Execution

### Virtual Environment & Dependencies
```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

# Install runtime dependencies
pip install -r requirements.txt
```

### Start Tony Locally
```powershell
python -m app.main
```
The server will start on `http://0.0.0.0:8080`.

### Health Check
In another terminal:
```powershell
curl http://localhost:8080/health
```
or in PowerShell:
```powershell
Invoke-RestMethod http://localhost:8080/health
```

---

## 5. Webhook Connectivity (Temporary Tunnels for Local Dev)

Because Cisco Webex sends webhooks via HTTPS over the public internet, expose local port `8080`:

### Option A: ngrok
```powershell
ngrok http 8080
```
Copy the forwarding HTTPS URL (e.g. `https://xxxx.ngrok-free.app`).

### Option B: Cloudflare Quick Tunnel
```powershell
cloudflared tunnel --url http://localhost:8080
```
Copy the temporary URL (e.g. `https://xxxx.trycloudflare.com`).

### Update `.env` & Automatic Webhook Provisioning
Add the tunnel URL to `.env`:
```env
PUBLIC_URL=https://xxxx.ngrok-free.app
```
Restart `python -m app.main`. Tony will automatically connect to Webex, delete obsolete hooks, and register the webhook pointing to `<PUBLIC_URL>/webex/webhook`.

---

## 6. Docker Deployment

Tony includes a production-ready, hardened Docker setup.

### Build and Run with Docker Compose
```powershell
# Build and start container in background
docker compose up -d --build

# View container status
docker compose ps

# Follow container logs
docker compose logs -f
```

### Stop Container
```powershell
docker compose down
```

### Persistence Guarantees
- The `./data` directory is bind-mounted to `/app/data` inside the container.
- Your Excel workbook (`data/testcases.xlsx`), SQLite idempotency database (`data/state.db`), backups (`data/backups/`), and screenshots (`data/attachments/`) survive container restarts and rebuilds.
- The Dockerfile runs as non-root user `tony` (UID 1000) for security.

---

## 7. Testing & Verification

### Run Functional & Edge Case Test Suite
Replays all 12 core scenarios and 13 edge cases using an isolated in-memory test chat adapter:
```powershell
python tests/test_bot.py
```
*(Optionally keep output workbook: `python tests/test_bot.py --out sample`)*.

### Run Webex Adapter & Webhook Mock Test Suite
Verifies signature validation, Webex message fetching, attachment downloading, and rate-limit retries against a mocked Webex API:
```powershell
python tests/test_webex.py
```

---

## 8. Suggested 3-Minute Live Demo Flow

1. **Verify Service Health**:
   Open terminal and run `curl http://localhost:8080/health` (verify status is `ok` and cases count matches).
2. **Add a Full Test Case**:
   In Webex, send:
   ```text
   @Tony add test case: Login fails with valid password on Safari.
   Steps: 1) open /login 2) enter valid user 3) click Sign in
   Expected: dashboard opens. Actual: spinner forever.
   module: Auth priority: high https://jira.example.com/BUG-482
   ```
   Show Tony's reply naming `TC-0001` and coordinates `Sheet: Auth | Row 2 | Cells A2:L2`.
3. **Trigger Missing Steps Guardrail**:
   Send: `@Tony add test case: Checkout button disabled on mobile. Expected: Button clickable. module: Billing`.
   Tony holds draft for 15 min and prompts for steps. Reply: `steps: 1) tap cart 2) tap checkout`. Tony saves `TC-0002`.
4. **Trigger Duplicate Warning**:
   Send a reworded variant: `@Tony add test case: Safari login hangs on valid password`. Tony flags similarity to `TC-0001` and offers `new`, `update`, or `cancel`. Reply `cancel`.
5. **Credential Masking**:
   Send: `@Tony add test case: Admin login with password=Secr3t! and Authorization: Bearer token123`.
   Open Excel and show that password and token were masked *before* persistence.
6. **Natural Language Search & Case View**:
   Ask: `@Tony where is the Safari login test?` $\rightarrow$ replies with `TC-0001`.
   Ask: `@Tony show TC-0001` $\rightarrow$ displays complete test case card.
7. **Status Update & Audit Trail**:
   Send: `@Tony update TC-0001 status: passed`. Open Excel `Audit` sheet to show user, timestamp, and old $\rightarrow$ new value.
8. **Soft Delete & Restore**:
   Send: `@Tony delete TC-0001` $\rightarrow$ confirm `yes`. Row is greyed out. Send: `@Tony restore TC-0001` $\rightarrow$ status restored to `Passed`.
9. **Excel File Lock Protection**:
   Open `data/testcases.xlsx` in Microsoft Excel. Send a new case in Webex. Tony detects the Windows file lock, notifies you that the write is queued, and automatically commits once Excel is closed!

---

## 9. Troubleshooting

| Symptom | Cause | Solution |
|---|---|---|
| Startup error: `RuntimeError: WEBEX_BOT_TOKEN is not set` | Missing token in `.env` | Ensure `.env` exists in the project root and has `WEBEX_BOT_TOKEN=...`. |
| Webex 401 Unauthorized at boot | Expired or regenerated token | Generate a fresh bot token at [developer.webex.com/my-apps](https://developer.webex.com/my-apps) and update `.env`. |
| Webhook endpoint returns 401 | Secret mismatch | Ensure `WEBEX_WEBHOOK_SECRET` in `.env` matches the secret registered on Webex. |
| Bot ignores messages in group space | Bot not @mentioned | In Webex group spaces, bots only receive messages where they are explicitly tagged (`@Tony ...`). In 1:1 direct chats, every message is received. |
| "Workbook is busy right now" warning | Excel or LibreOffice has workbook locked | Close Microsoft Excel. Tony's FIFO write queue will retry every 10s and save automatically. |
| Webhooks fail with SSL/connection errors | Tunnel stopped | Restart your `ngrok` or `cloudflared` tunnel and ensure `PUBLIC_URL` matches the active URL. |
