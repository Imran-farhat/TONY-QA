"""Chat-platform contract. Add Slack/Teams by implementing this + converting events to IncomingMessage."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ChatAdapter(ABC):
    @abstractmethod
    def send(self, room_id: str, text: str) -> None:
        """Post a plain-text reply into a room / DM."""
