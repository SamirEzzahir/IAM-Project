"""Durable, bounded storage for completed public job snapshots."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


class JobStore:
    def __init__(self, path: Path, max_jobs: int = 20):
        self.path = path
        self.max_jobs = max_jobs

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def save(self, job_id: str, kind: str, updated_at: str, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """INSERT INTO jobs(job_id, kind, updated_at, payload)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(job_id) DO UPDATE SET
                           kind=excluded.kind,
                           updated_at=excluded.updated_at,
                           payload=excluded.payload""",
                    (job_id, kind, updated_at, encoded),
                )
                connection.execute(
                    """DELETE FROM jobs WHERE job_id IN (
                        SELECT job_id FROM jobs ORDER BY updated_at DESC LIMIT -1 OFFSET ?
                    )""",
                    (self.max_jobs,),
                )

    def load(self, job_id: str) -> dict | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
