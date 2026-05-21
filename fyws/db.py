from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "jobs.sqlite3"
SCHEMA_PATH = ROOT / "schema.sql"
_CONNECT_INIT_LOCK = threading.Lock()


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
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
