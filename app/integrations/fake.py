"""In-memory chat adapter used for offline testing without network."""
from __future__ import annotations

import itertools
import threading

from app.integrations.base import ChatAdapter
from app.models import Attachment, IncomingMessage


class FakeChat(ChatAdapter):
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.log: list[tuple[str, str]] = []   # Transcript: ("USER"|"TONY", text)
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def send(self, room_id: str, text: str) -> None:
        with self._lock:
            self.sent.append((room_id, text))
            self.log.append(("TONY", text))

    def message(self, text: str, user: str = "priya@example.com", room: str = "room-1",
                event_id: str | None = None, attachments: list[Attachment] | None = None,
                mentioned: bool = True, direct: bool = False) -> IncomingMessage:
        with self._lock:
            self.log.append(("USER", f"{user}: @tony {text}"))
        return IncomingMessage(event_id=event_id or f"evt-{next(self._ids)}", room_id=room, user_id=user,
                               user_name=user, text=text, attachments=attachments or [],
                               is_direct=direct, mentioned=mentioned)

    def replies_since(self, mark: int) -> list[str]:
        with self._lock:
            return [t for _, t in self.sent[mark:]]

    @property
    def mark(self) -> int:
        with self._lock:
            return len(self.sent)

