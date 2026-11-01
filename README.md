# Tony: QA Test-Case Keeper Bot 🤖📊

> **Turn team chat into clean, organized Excel test cases in real-time.**  
> Built for Cisco Webex, powered by FastAPI and openpyxl.

---

### What it looks like:

```text
User:  @Tony add test case: Login fails with valid password on Safari.
       Steps: 1) open /login 2) enter valid user 3) click Sign in
       Expected: dashboard opens. Actual: spinner forever.
       module: Auth   priority: high   https://jira.example.com/BUG-482

Tony:  Saved as TC-0001
       File: testcases.xlsx | Sheet: Auth | Row 2 | Cells A2:L2
       Link: BUG-482
```

---

## ⚡ Quickstart (Run in 60 seconds)

### 1. Clone & Install
```powershell
# Navigate to project
cd tony

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure `.env`
Copy the sample file and add your Webex credentials:
```powershell
cp .env.example .env
```
In `.env`, set:
- `WEBEX_BOT_TOKEN=...` (from [developer.webex.com](https://developer.webex.com/))
- `WEBEX_WEBHOOK_SECRET=...` (any random string)

### 3. Run Tony
```powershell
python -m app.main
```
Tony starts on `http://localhost:8080` and connects directly to your Webex bot!

---

## 💬 Commands Cheat Sheet

Talk to Tony in any Webex space or direct 1:1 chat:

| You Say | What Tony Does |
|---|---|
| `@Tony add test case: ...` | Parses fields, checks for duplicates, and saves directly to Excel |
| `@Tony where is the Safari login test?` | Searches your workbook and returns the exact sheet, row, and status |
| `@Tony show TC-0001` | Displays the complete details card for that test case |
| `@Tony list high priority Auth cases` | Shows a filtered list (by priority, module, or status) |
| `@Tony update TC-0001 status: passed` | Edits the cell in Excel and logs old/new values in the `Audit` sheet |
| `@Tony delete TC-0001` | Soft-deletes the row (greys it out in Excel); confirm with `yes` |
| `@Tony restore TC-0001` | Brings back a deleted test case to its previous status |
| `@Tony help` | Prints the quick help guide |

---

## ✨ Key Features & Guardrails

- 🛡️ **Zero Guesswork**: Tony never invents steps or expected results. If you leave them out, Tony holds your draft for 15 minutes and asks you to supply them.
- 🔒 **Automatic Privacy Masking**: Passwords (`password=...`), bearer tokens, API keys, and credit card numbers are masked *before* touching Excel or logs.
- ⚠️ **Duplicate Detection**: Flags similar existing test cases to prevent filing the same bug twice.
- 📑 **Self-Healing Excel Workbook**:
  - **Module Sheets**: Organizes cases into separate sheets (`Auth`, `Billing`, `UI`, `General`).
  - **Clickable Index**: Rebuilds an `Index` sheet with Excel hyperlinks (`#'Auth'!A2`) jumping to the row.
  - **Audit Trail**: Every change (`ADD`, `UPDATE`, `DELETE`, `RESTORE`) is permanently logged with timestamps and user details.
- 🔐 **Never Corrupts Files**: If someone has `testcases.xlsx` open in Microsoft Excel, Tony queues the write, informs the user, and retries automatically every 10 seconds.
- 🔄 **Rolling Backups**: Automatically keeps the last 20 versions of your workbook in `data/backups/`.

---

## 🏗️ How it Works (Under the Hood)

Tony uses a clean, decoupled architecture:

```text
Webex Chat ──► FastAPI Webhook ──► Security Masking ──► Command Parser ──► Write Queue ──► Excel Workbook
                     │                                                                         │
                     └────────────────────── Webex Response ◄──────────────────────────────────┘
```

1. **Webhook Listener (`app/main.py`)**: Receives the event, validates the Webex HMAC signature, and replies immediately to avoid timeouts.
2. **Security & Parser (`app/services/`)**: Sanitizes credentials, strips the bot mention, and extracts test fields (Steps, Expected, Actual, Module).
3. **Bot Orchestration (`app/services/bot_service.py`)**: Verifies required fields, checks for duplicates, and manages conversational drafts in SQLite.
4. **Thread-Safe Storage (`app/storage/`)**: Serializes writes through a background FIFO queue, preventing multi-user file conflicts.

---

## 🐳 Running with Docker

Prefer containers? Tony includes a production-ready, non-root Docker setup:

```powershell
# Build and run in the background
docker compose up -d --build

# View real-time logs
docker compose logs -f

# Stop the container
docker compose down
```

> **Your data is safe:** All Excel workbooks, SQLite history, attachments, and backups are stored in the `./data` folder on your computer and persist across container rebuilds.

---

## 🧪 Testing

Run Tony's test suites with a single command (no internet or Webex required):

```powershell
# Run the 28 core scenarios and edge cases (fake chat adapter)
python tests/test_bot.py

# Run the Webex adapter and signature verification tests
python tests/test_webex.py
```

---

## 📂 Project Organization

```text
tony/
├── app/                  # Application source code
│   ├── main.py           # FastAPI entrypoint & webhook handler
│   ├── config.py         # Settings & environment loader
│   ├── models.py         # Data models (IncomingMessage, Draft, CaseRecord)
│   ├── services/         # Bot logic, parser, privacy masking, similarity
│   ├── integrations/     # Cisco Webex API & mock chat adapters
│   └── storage/          # Excel engine, FIFO queue, and SQLite state
├── data/                 # Workbooks, backups, and attachments (git-ignored)
├── tests/                # Automated test suites (28/28 passing)
├── docs/                 # Detailed setup & interview guide (SETUP.md)
├── Dockerfile            # Container definition
├── docker-compose.yml    # Docker orchestration
└── README.md             # This guide
```

---

## 📖 Need More Details?

For in-depth guides on creating a Webex bot, setting up temporary tunnels (`ngrok`/`cloudflared`), and deep architectural interview explainers, check out **[docs/SETUP.md](docs/SETUP.md)**.
