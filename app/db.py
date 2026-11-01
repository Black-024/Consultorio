"""Persistencia en SQLite (un solo archivo, sin servidor: lo más simple para un consultorio)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    starts_at TEXT NOT NULL,                 -- UTC, ISO 8601
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | cancelled
    calendar_event_id TEXT,
    r7_status TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | failed | skipped
    r7_sent_at TEXT,
    r7_attempts INTEGER NOT NULL DEFAULT 0,
    r1_status TEXT NOT NULL DEFAULT 'pending',
    r1_sent_at TEXT,
    r1_attempts INTEGER NOT NULL DEFAULT 0,
    delivery_problem TEXT,                   -- null | not_on_whatsapp | send_failed
    no_response_alerted_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                      -- r7 | r1
    wamid TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'sent'
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    appointment_id INTEGER,
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
