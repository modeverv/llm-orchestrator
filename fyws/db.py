from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from types import TracebackType


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "jobs.sqlite3"
SCHEMA_PATH = ROOT / "schema.sql"
_CONNECT_INIT_LOCK = threading.Lock()


class ClosingConnection(sqlite3.Connection):
    def __enter__(self) -> ClosingConnection:
        self._fyws_context_depth = getattr(self, "_fyws_context_depth", 0) + 1
        return super().__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        suppress = super().__exit__(exc_type, exc_value, traceback)
        depth = getattr(self, "_fyws_context_depth", 1) - 1
        self._fyws_context_depth = depth
        if depth <= 0:
            self.close()
        return bool(suppress)


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    with _CONNECT_INIT_LOCK:
        _execute_with_lock_retry(conn, "PRAGMA busy_timeout = 30000")
        _execute_with_lock_retry(conn, "PRAGMA foreign_keys = ON")
        _execute_with_lock_retry(conn, "PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(db_path) as conn:
        _executescript_with_lock_retry(conn, schema)
        _migrate_jobs_status_discarded(conn)


def _execute_with_lock_retry(conn: sqlite3.Connection, sql: str, attempts: int = 20) -> None:
    for attempt in range(attempts):
        try:
            conn.execute(sql)
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(0.1)


def _executescript_with_lock_retry(conn: sqlite3.Connection, sql: str, attempts: int = 20) -> None:
    for attempt in range(attempts):
        try:
            conn.executescript(sql)
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(0.1)


def _migrate_jobs_status_discarded(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
    ).fetchone()
    if row is None or "'discarded'" in row["sql"]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE jobs_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    prompt_path TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode IN ('read', 'write', 'deploy')),
                    worker TEXT NOT NULL DEFAULT 'gemini',
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'queued',
                            'running',
                            'succeeded',
                            'failed',
                            'waiting_human',
                            'discarded'
                        )
                    ),
                    safe_score REAL NOT NULL,
                    c_score REAL NOT NULL,
                    o_score REAL NOT NULL,
                    i_score REAL NOT NULL,
                    ownership_paths TEXT NOT NULL DEFAULT '[]',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    prompt_template_id INTEGER,
                    gemini_session_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO jobs_new(
                    id, project, prompt_path, cwd, mode, worker, status, safe_score,
                    c_score, o_score, i_score, ownership_paths, attempts,
                    prompt_template_id, gemini_session_id, last_error,
                    created_at, updated_at, started_at, finished_at
                )
                SELECT
                    id, project, prompt_path, cwd, mode, worker, status, safe_score,
                    c_score, o_score, i_score, ownership_paths, attempts,
                    prompt_template_id, gemini_session_id, last_error,
                    created_at, updated_at, started_at, finished_at
                FROM jobs
                """
            )
            conn.execute("DROP TABLE jobs")
            conn.execute("ALTER TABLE jobs_new RENAME TO jobs")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
