"""Integrations package: Webex and fake test adapters."""
from __future__ import annotations

from app.integrations.base import ChatAdapter
from app.integrations.fake import FakeChat
from app.integrations.webex import WebexAdapter

__all__ = ["ChatAdapter", "FakeChat", "WebexAdapter"]
