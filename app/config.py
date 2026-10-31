"""Configuration loader for Tony application.

Loads environment variables from .env and system environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_get_env("DATA_DIR", "./data")))
    workbook_name: str = field(default_factory=lambda: _get_env("WORKBOOK_NAME", "testcases.xlsx"))
    timezone: str = field(default_factory=lambda: _get_env("TZ", "Asia/Kolkata"))

    # Webex
    webex_token: str = field(default_factory=lambda: _get_env("WEBEX_BOT_TOKEN", ""))
    webex_webhook_secret: str = field(default_factory=lambda: _get_env("WEBEX_WEBHOOK_SECRET", ""))
    public_url: str = field(default_factory=lambda: _get_env("PUBLIC_URL", ""))
    workbook_url: str = field(default_factory=lambda: _get_env("WORKBOOK_URL", ""))

    # Behaviour
    backups_keep: int = field(default_factory=lambda: int(_get_env("BACKUPS_KEEP", "20")))
    duplicate_threshold: float = field(default_factory=lambda: float(_get_env("DUPLICATE_THRESHOLD", "0.60")))
    search_threshold: float = field(default_factory=lambda: float(_get_env("SEARCH_THRESHOLD", "0.50")))
    lock_retry_seconds: float = field(default_factory=lambda: float(_get_env("LOCK_RETRY_SECONDS", "10")))
    lock_max_wait_seconds: float = field(default_factory=lambda: float(_get_env("LOCK_MAX_WAIT_SECONDS", "900")))
    pending_ttl_seconds: int = field(default_factory=lambda: int(_get_env("PENDING_TTL_SECONDS", "900")))
    max_attachment_mb: int = field(default_factory=lambda: int(_get_env("MAX_ATTACHMENT_MB", "10")))
    mask_emails: bool = field(default_factory=lambda: _get_env("MASK_EMAILS", "false").lower() == "true")

    # Operations
    port: int = field(default_factory=lambda: int(_get_env("PORT", "8080")))
    ping_url: str = field(default_factory=lambda: _get_env("HEALTHCHECK_PING_URL", ""))
    log_level: str = field(default_factory=lambda: _get_env("LOG_LEVEL", "INFO"))

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

