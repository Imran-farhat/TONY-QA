"""Chat platform abstract contract."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ChatAdapter(ABC):
    @abstractmethod
    def send(self, room_id: str, text: str) -> None:
        """Post a plain-text reply into a room or 1:1 direct chat."""

