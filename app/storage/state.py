"""SQLite state persistence for processed events and pending conversations."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional

from app.config import Config


class State:
    def __init__(self, cfg: Config):
        self.path = str(cfg.state_db)
        self.ttl = cfg.pending_ttl_seconds
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, ts REAL)")
            c.execute("CREATE TABLE IF NOT EXISTS pending(room_id TEXT, user_id TEXT, payload TEXT, expires REAL,"
                      " PRIMARY KEY(room_id, user_id))")
        self.prune()

    def _conn(self):
        return sqlite3.connect(self.path, timeout=30, check_same_thread=False)

    def claim_event(self, event_id: str) -> bool:
        """True the first time an event id is seen, False for every redelivery (atomic)."""
        with self._lock, self._conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO events(event_id, ts) VALUES(?, ?)", (event_id, time.time()))
            return cur.rowcount == 1

    def prune(self, days: int = 14):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM events WHERE ts < ?", (time.time() - days * 86400,))
            c.execute("DELETE FROM pending WHERE expires < ?", (time.time(),))

    def set_pending(self, room: str, user: str, payload: dict):
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO pending VALUES(?,?,?,?)",
                      (room, user, json.dumps(payload), time.time() + self.ttl))

    def get_pending(self, room: str, user: str) -> Optional[dict]:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT payload, expires FROM pending WHERE room_id=? AND user_id=?", (room, user)).fetchone()
        if not row or row[1] < time.time():
            return None
        return json.loads(row[0])

    def clear_pending(self, room: str, user: str):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM pending WHERE room_id=? AND user_id=?", (room, user))

