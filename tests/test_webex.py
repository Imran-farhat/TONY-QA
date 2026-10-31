"""Exercises the real Webex adapter and FastAPI webhook against a mocked Webex REST API.

Tests signature validation, duplicate delivery, field extraction, credential masking,
attachment downloading with auth, response posting, and health endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from fastapi.testclient import TestClient

from app.config import Config
from app.main import build_app

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
SECRET = "s3cret"
posted: list[dict] = []
auth_seen: list[str] = []


class R:
    def __init__(self, code=200, body=None, headers=None, raw=b""):
        self.status_code = code
        self._b = body
        self.headers = headers or {}
        self._raw = raw
        self.text = json.dumps(body or {})

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, n):
        yield self._raw


def fake_request(self, method, url, **kw):
    auth_seen.append(self.headers.get("Authorization", ""))
    if url.endswith("/people/me"):
        return R(body={"id": "BOT", "displayName": "Tony"})
    if url.endswith("/webhooks") and method == "GET":
        return R(body={"items": []})
    if url.endswith("/webhooks") and method == "POST":
        return R(body={"id": "H1"})
    if "/messages/M1" in url or "/messages/M2" in url:
        return R(body={
            "id": url.rsplit("/", 1)[1],
            "roomId": "R1",
            "personId": "P1",
            "personEmail": "priya@example.com",
            "roomType": "group",
            "mentionedPeople": ["BOT"],
            "files": ["https://webexapis.com/v1/contents/F1"],
            "text": "Tony add test case: Login fails on Safari. Steps: 1) open /login 2) sign in "
                    "Expected: dashboard Actual: spinner module: Auth priority: high password=Hunter2"
        })
    if "/contents/F1" in url:
        return R(headers={"Content-Disposition": 'attachment; filename="shot.png"', "Content-Type": "image/png"}, raw=PNG)
    if url.endswith("/messages") and method == "POST":
        posted.append(kw["json"])
        return R(body={"id": "OUT"})
    return R(404, {})


def main() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")

    root = Path(tempfile.mkdtemp(prefix="tony-webex-"))
    cfg = Config(data_dir=root, webex_token="BOT-TOKEN", webex_webhook_secret=SECRET, public_url="https://example.test")
    with mock.patch.object(requests.Session, "request", fake_request):
        app = build_app(cfg)
        with TestClient(app) as client:
            body = json.dumps({"resource": "messages", "event": "created", "data": {"id": "M1", "personId": "P1"}}).encode()
            bad = client.post("/webex/webhook", content=body, headers={"X-Spark-Signature": "nope"})
            check("webhook with a bad signature is rejected (401)", bad.status_code == 401)
            sig = hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest()
            for _ in range(2):   # Webex delivering the same event twice
                client.post("/webex/webhook", content=body, headers={"X-Spark-Signature": sig})
            time.sleep(1.5)
            from openpyxl import load_workbook
            wb = load_workbook(cfg.workbook_path)
            rows = [r for r in wb["Auth"].iter_rows(min_row=2, values_only=True) if r[0]]
            blob = " ".join(str(c) for r in rows for c in r)
            check("duplicate webhook delivery -> exactly one row", len(rows) == 1)
            check("bot name stripped, fields parsed from Webex message", rows and rows[0][1] == "Login fails on Safari" and rows[0][3] == "High")
            check("secret masked before it reached Excel", "Hunter2" not in blob)
            check("screenshot downloaded WITH the bot token and saved", (root / "attachments/TC-0001_1.png").read_bytes() == PNG
                  and "Bearer BOT-TOKEN" in auth_seen)
            check("reply posted back to the right room with exact location",
                  len(posted) == 1 and posted[0]["roomId"] == "R1" and "Sheet: Auth | Row 2" in posted[0]["text"])
            h = client.get("/health")
            check("/health returns ok + case count", h.status_code == 200 and h.json()["cases"] == 1 and h.json()["status"] == "ok")
            own = json.dumps({"resource": "messages", "event": "created", "data": {"id": "M2", "personId": "BOT"}}).encode()
            n = len(posted)
            client.post("/webex/webhook", content=own, headers={"X-Spark-Signature": hmac.new(SECRET.encode(), own, hashlib.sha1).hexdigest()})
            time.sleep(0.5)
            check("bot ignores its own messages", len(posted) == n)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

