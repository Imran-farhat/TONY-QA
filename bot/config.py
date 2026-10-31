"""Configuration, loaded from environment (.env). No secrets live in the repo."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is optional at runtime (docker passes env directly)
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _f(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_f("DATA_DIR", "./data")))
    workbook_name: str = field(default_factory=lambda: _f("WORKBOOK_NAME", "testcases.xlsx"))
    timezone: str = field(default_factory=lambda: _f("TZ", "Asia/Kolkata"))

    # Webex
    webex_token: str = field(default_factory=lambda: _f("WEBEX_BOT_TOKEN", ""))
    webex_webhook_secret: str = field(default_factory=lambda: _f("WEBEX_WEBHOOK_SECRET", ""))
    public_url: str = field(default_factory=lambda: _f("PUBLIC_URL", ""))
    workbook_url: str = field(default_factory=lambda: _f("WORKBOOK_URL", ""))

    # Behaviour
    backups_keep: int = field(default_factory=lambda: int(_f("BACKUPS_KEEP", "20")))
    duplicate_threshold: float = field(default_factory=lambda: float(_f("DUPLICATE_THRESHOLD", "0.60")))
    search_threshold: float = field(default_factory=lambda: float(_f("SEARCH_THRESHOLD", "0.50")))
    lock_retry_seconds: float = field(default_factory=lambda: float(_f("LOCK_RETRY_SECONDS", "10")))
    lock_max_wait_seconds: float = field(default_factory=lambda: float(_f("LOCK_MAX_WAIT_SECONDS", "900")))
    pending_ttl_seconds: int = field(default_factory=lambda: int(_f("PENDING_TTL_SECONDS", "900")))
    max_attachment_mb: int = field(default_factory=lambda: int(_f("MAX_ATTACHMENT_MB", "10")))
    mask_emails: bool = field(default_factory=lambda: _f("MASK_EMAILS", "false").lower() == "true")

    # Ops
    port: int = field(default_factory=lambda: int(_f("PORT", "8080")))
    ping_url: str = field(default_factory=lambda: _f("HEALTHCHECK_PING_URL", ""))
    log_level: str = field(default_factory=lambda: _f("LOG_LEVEL", "INFO"))

    @property
    def workbook_path(self) -> Path:
        return self.data_dir / self.workbook_name

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "attachments" / "_staging"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state.db"
