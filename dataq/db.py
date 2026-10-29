"""SQLite session log for saved profiles."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DATAQ_DB", os.path.join(BASE_DIR, "sessions.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    score REAL NOT NULL,
    verdict TEXT NOT NULL,
    rows_count INTEGER NOT NULL DEFAULT 0,
    columns_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    self_rating INTEGER,
    profile_json TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def add_session(profile: Dict[str, Any], notes: str = "", self_rating: Optional[int] = None) -> int:
    meta = profile.get("meta", {})
    scores = profile.get("scores", {})
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO sessions (created_at, dataset_name, purpose, score, verdict, rows_count,"
            " columns_count, notes, self_rating, profile_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                meta.get("dataset_name", "untitled-dataset"),
                meta.get("purpose", "general"),
                float(scores.get("overall", 0)),
                scores.get("verdict", "needs_rework"),
                int(meta.get("rows", 0)),
                int(meta.get("columns", 0)),
                notes or "",
                int(self_rating) if self_rating is not None else None,
                json.dumps(profile, default=str),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_sessions(limit: int = 100) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, created_at, dataset_name, purpose, score, verdict, rows_count, columns_count,"
            " notes, self_rating FROM sessions ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_session(session_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (int(session_id),)).fetchone()
        if row is None:
            return None
        record = dict(row)
        try:
            record["profile"] = json.loads(record.get("profile_json") or "{}")
        except json.JSONDecodeError:
            record["profile"] = {}
        record.pop("profile_json", None)
        return record
    finally:
        conn.close()


def update_session(session_id: int, notes: Optional[str] = None,
                   self_rating: Optional[int] = None) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        fields = []
        values: List[Any] = []
        if notes is not None:
            fields.append("notes = ?")
            values.append(notes)
        if self_rating is not None:
            fields.append("self_rating = ?")
            values.append(int(self_rating))
        if fields:
            values.append(int(session_id))
            conn.execute("UPDATE sessions SET %s WHERE id = ?" % ", ".join(fields), values)
            conn.commit()
        return get_session(session_id)
    finally:
        conn.close()
