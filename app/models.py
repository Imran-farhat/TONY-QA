"""Platform-neutral data models for Tony."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class IncomingMessage:
    event_id: str            # Unique per chat message (used for idempotency)
    room_id: str
    user_id: str
    user_name: str           # Display name or email
    text: str                # Mention prefix already stripped by adapter
    attachments: list[Attachment] = field(default_factory=list)
    is_direct: bool = False
    mentioned: bool = True

    @property
    def addressed(self) -> bool:
        """Tony acts only when mentioned in spaces or messaged in direct 1:1 chat."""
        return self.is_direct or self.mentioned


ESSENTIAL = (
    ("title", "a title (what is the bug / scenario?)"),
    ("steps", "Steps to reproduce"),
    ("expected", "Expected result"),
)


@dataclass
class Draft:
    title: str = ""
    module: str = ""
    priority: str = ""
    steps: str = ""
    expected: str = ""
    actual: str = ""
    environment: str = ""
    links: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)   # Staged relative files
    status: str = "Open"
    reporter: str = ""
    source_key: str = ""     # "<event_id>#<n>" -> idempotency inside workbook
    masked: int = 0

    def missing(self) -> list[str]:
        return [label for key, label in ESSENTIAL if not getattr(self, key)]

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> Draft:
        return Draft(**{k: v for k, v in d.items() if k in Draft.__dataclass_fields__})


@dataclass
class CaseRecord:
    """One row as persisted in the Excel workbook."""
    tc_id: str
    sheet: str
    row: int
    title: str = ""
    module: str = ""
    priority: str = ""
    steps: str = ""
    expected: str = ""
    actual: str = ""
    environment: str = ""
    links: str = ""
    attachments: str = ""
    status: str = ""
    reporter: str = ""

    @property
    def deleted(self) -> bool:
        return self.status.strip().lower() == "deleted"

    @property
    def cell_range(self) -> str:
        return f"A{self.row}:L{self.row}"
