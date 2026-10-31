"""Cisco Webex adapter (REST + webhooks). The ONLY file that knows Webex.

Notes baked in:
* In group spaces a bot only receives messages that @mention it; in 1:1 spaces it sees everything.
* The webhook only carries a message id: we GET the message to read text + file URLs.
* Files must be downloaded with the bot token (Authorization header).
* Webhook delivery can repeat -> the message id is used as the idempotency key upstream.
* Webex disables webhooks that fail repeatedly -> ensure_webhook() runs at start and every few
  minutes and re-creates / re-enables ours (this is the "self-healing reconnect").
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from typing import Optional

import requests

from ..config import Config
from ..models import Attachment, IncomingMessage
from .base import ChatAdapter

log = logging.getLogger("tony.webex")
API = "https://webexapis.com/v1"
HOOK_NAME = "tony-messages"
MAX_MSG_BYTES = 6500


class WebexAdapter(ChatAdapter):
    def __init__(self, cfg: Config):
        if not cfg.webex_token:
            raise RuntimeError("WEBEX_BOT_TOKEN is not set (see .env.example)")
        self.cfg = cfg
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {cfg.webex_token}"})
        self.me: dict = {}
        self._people: dict[str, str] = {}
        self.webhook_status = "unknown"

    # ------------------------------------------------------------------ http
    def _req(self, method: str, path: str, **kw) -> requests.Response:
        url = path if path.startswith("http") else f"{API}{path}"
        for attempt in range(4):
            r = self.s.request(method, url, timeout=30, **kw)
            if r.status_code == 429:                       # rate limited
                wait = int(r.headers.get("Retry-After", "2"))
                log.warning("Webex 429, sleeping %ss", wait)
                time.sleep(min(wait, 30))
                continue
            if r.status_code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return r
        return r

    def start(self):
        r = self._req("GET", "/people/me")
        r.raise_for_status()
        self.me = r.json()
        log.info("Webex bot identity: %s (%s)", self.me.get("displayName"), self.me.get("id", "")[-8:])
        self.ensure_webhook()

    # ------------------------------------------------------------- webhooks
    def ensure_webhook(self):
        """Idempotently make sure exactly one active webhook points at PUBLIC_URL."""
        if not self.cfg.public_url:
            self.webhook_status = "manual (PUBLIC_URL not set)"
            return
        target = self.cfg.public_url.rstrip("/") + "/webex/webhook"
        try:
            r = self._req("GET", "/webhooks", params={"max": 100})
            r.raise_for_status()
            ours = [h for h in r.json().get("items", []) if h.get("name") == HOOK_NAME]
            good = [h for h in ours if h["targetUrl"] == target and h.get("status") == "active"]
            for h in ours:                                    # remove stale / disabled / duplicate hooks
                if h not in good[:1]:
                    self._req("DELETE", f"/webhooks/{h['id']}")
            if not good:
                body = {"name": HOOK_NAME, "targetUrl": target, "resource": "messages", "event": "created",
                        "secret": self.cfg.webex_webhook_secret or None}
                cr = self._req("POST", "/webhooks", json={k: v for k, v in body.items() if v})
                cr.raise_for_status()
                log.info("Webex webhook (re)created -> %s", target)
            self.webhook_status = "active"
        except Exception as e:
            self.webhook_status = f"error: {e}"
            log.exception("ensure_webhook failed")

    def verify_signature(self, body: bytes, signature: Optional[str]) -> bool:
        if not self.cfg.webex_webhook_secret:
            return True
        if not signature:
            return False
        mac = hmac.new(self.cfg.webex_webhook_secret.encode(), body, hashlib.sha1).hexdigest()
        return hmac.compare_digest(mac, signature)

    # ---------------------------------------------------------------- inbound
    def _person_label(self, person_id: str, email: str) -> str:
        return email or self._people.get(person_id) or person_id

    def parse_event(self, payload: dict) -> Optional[IncomingMessage]:
        data = payload.get("data", {})
        if payload.get("resource") != "messages" or payload.get("event") != "created":
            return None
        if data.get("personId") == self.me.get("id"):          # never react to our own messages
            return None
        r = self._req("GET", f"/messages/{data['id']}")
        if r.status_code != 200:
            log.warning("could not fetch message %s: %s", data.get("id"), r.status_code)
            return None
        m = r.json()
        direct = m.get("roomType") == "direct"
        mentioned = direct or self.me.get("id") in (m.get("mentionedPeople") or [])
        text = self._strip_bot_name(m.get("text", ""))
        atts = []
        for url in m.get("files", []) or []:
            a = self._download(url)
            if a:
                atts.append(a)
        return IncomingMessage(event_id=m["id"], room_id=m["roomId"], user_id=m["personId"],
                               user_name=self._person_label(m["personId"], m.get("personEmail", "")),
                               text=text, attachments=atts, is_direct=direct, mentioned=mentioned)

    def _strip_bot_name(self, text: str) -> str:
        name = (self.me.get("displayName") or "tony").strip()
        for n in {name, name.split()[0]}:
            text = re.sub(rf"(?i)^\s*@?{re.escape(n)}\b[\s,:\-]*", "", text, count=1)
        return text.strip()

    def _download(self, url: str) -> Optional[Attachment]:
        cap = self.cfg.max_attachment_mb * 1024 * 1024
        try:
            r = self._req("GET", url, stream=True)
            if r.status_code != 200:
                log.warning("file download failed: %s", r.status_code)
                return None
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > cap + 1:
                    break
            cd = r.headers.get("Content-Disposition", "")
            m = re.search(r'filename="?([^";]+)"?', cd)
            return Attachment(filename=m.group(1) if m else "file",
                              content_type=r.headers.get("Content-Type", ""), data=data)
        except Exception:
            log.exception("file download error")
            return None

    # --------------------------------------------------------------- outbound
    def send(self, room_id: str, text: str) -> None:
        for part in self._split(text):
            r = self._req("POST", "/messages", json={"roomId": room_id, "text": part})
            if r.status_code >= 300:
                log.error("send failed %s %s", r.status_code, r.text[:200])

    @staticmethod
    def _split(text: str) -> list[str]:
        parts, cur = [], ""
        for line in text.split("\n"):
            if len((cur + line).encode()) > MAX_MSG_BYTES:
                parts.append(cur)
                cur = ""
            cur += line + "\n"
        parts.append(cur)
        return [p.strip() for p in parts if p.strip()]
