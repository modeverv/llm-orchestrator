from __future__ import annotations

import os
import sqlite3


def check_conflict(conn: sqlite3.Connection, project: str, cwd: str, mode: str) -> bool:
    if mode == "read":
        rows = conn.execute(
            "SELECT mode FROM locks WHERE project = ? AND cwd = ?",
            (project, cwd),
        ).fetchall()
        return any(row["mode"] in ("write", "deploy") for row in rows)
    return (
        conn.execute(
            "SELECT 1 FROM locks WHERE project = ? AND cwd = ? LIMIT 1",
            (project, cwd),
        ).fetchone()
        is not None
    )


def acquire_lock(conn: sqlite3.Connection, job_id: int, project: str, cwd: str, mode: str) -> bool:
    with conn:
        if check_conflict(conn, project, cwd, mode):
            return False
        conn.execute(
            "INSERT INTO locks(project, cwd, mode, job_id, owner) VALUES (?, ?, ?, ?, ?)",
            (project, cwd, mode, job_id, f"{os.uname().nodename}:{os.getpid()}"),
        )
    return True


def release_lock(conn: sqlite3.Connection, job_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM locks WHERE job_id = ?", (job_id,))
