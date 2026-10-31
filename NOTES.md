# NOTES: Tony design choices, assumptions, limits

**Stack:** Python 3.11+, FastAPI (webhook + `/health`), openpyxl, filelock, SQLite (state). No LLM in this build.

## Design choices
* **Chat layer isolated.** `service.py` only sees `IncomingMessage` and `ChatAdapter.send()`; all Webex code is in `chat/webex.py`. The same service runs under a fake adapter for the offline test script.
* **Sheets are the source of truth; rows located by TC ID.** The `Index` sheet is a lookup table rebuilt from the sheets after every write, so manual sorting/inserting/deleting or a damaged Index never gives a wrong location (tested).
* **Safe writes.** One writer thread + file lock; backup, temp file, atomic replace. A locked workbook (Excel open) queues the job, tells the user, retries.
* **Idempotency in two layers:** claimed Webex message id (SQLite) and `<message id>#n` stored in `Audit` (survives a lost DB).
* **Rule-based parser, not an LLM.** Deterministic, offline, cannot invent steps or expected results. Free-text without labels is not guessed: Tony asks.
* **Masking happens first**, on the raw text, before parsing/saving (and before any future LLM call).
* **Nothing is lost:** soft delete, `restore`, Audit with old/new values, IDs never reused, 20 rolling backups.

## Assumptions
* Row 1 is the header, so the first case is on row 2; IDs start at `TC-0001`.
* No module given -> sheet `General`; no priority -> `Medium` (both stated in the reply). Environment is filled only from literal mentions (e.g. "Safari 17", "macOS").
* Reporter = e-mail local part (`@priya`); date in `TZ` (default Asia/Kolkata).
* Duplicate = title/steps similarity >= 0.60; search match >= 0.50 query coverage. Thresholds are env-tunable.
* In a multi-case message, attachments go to the first case. "Update" on a duplicate overwrites steps/expected/actual with the new text (old text stays in Audit).
* Changing a case's module (moving between sheets) is not supported; delete and re-add.
* One Tony instance per workbook; Webex needs a public HTTPS URL (ngrok/cloudflared for the demo).

## Known limits
* Parser needs labels (`steps:`, `expected:`) or a follow-up answer; very free-form prose will just trigger questions.
* The write queue is in memory: a crash while the workbook is locked loses the queued request (user is told it was queued).
* Duplicate check reads the saved workbook, so two near-identical messages sent in the same second can both be saved.
* Screenshots are stored as-is; secrets visible inside an image are not detected (no OCR).
* File lock protects processes on one host, not a workbook on a network share/SharePoint opened by many editors.
* Verification: offline replay of the 12 samples + 13 edge cases, and the Webex adapter against a mocked Webex API. The live Webex space run is in the screen recording.

## With more time
Persistent outbox for queued writes; LLM extraction behind masking with a "confirm these fields" card; Adaptive Cards buttons (new/update/cancel); Microsoft Graph so the workbook can be edited live on SharePoint; Webex websocket mode (no public URL); OCR + masking of screenshots; atomic duplicate check inside the writer; metrics and alerting; Slack/Teams adapters; CI running the test scripts and integration tests.
