"""DNC event log (SQLite in config/events.db). Ported from serial-dnc's db.py."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config

DB_PATH = os.path.join(config.CONFIG_DIR, "events.db")
KEEP = 20000

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    machine_id TEXT,
    machine_name TEXT,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


@contextmanager
def connect():
    with _lock:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()


def log_event(machine: dict | None, level: str, message: str):
    with connect() as c:
        c.execute(
            "INSERT INTO events (machine_id, machine_name, level, message) VALUES (?, ?, ?, ?)",
            (machine["id"] if machine else None, machine["name"] if machine else None, level, message),
        )
        # Keep the log from growing forever.
        c.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", (KEEP,))


def recent_events(limit: int = 200, machine_id: str | None = None) -> list[dict]:
    with connect() as c:
        if machine_id is None:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = c.execute("SELECT * FROM events WHERE machine_id = ? ORDER BY id DESC LIMIT ?",
                             (machine_id, limit))
        return [dict(r) for r in rows]
