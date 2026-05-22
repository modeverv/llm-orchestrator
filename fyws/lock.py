from __future__ import annotations

import os
import sqlite3
import time
import calendar


DEFAULT_STALE_LOCK_SECONDS = 6 * 60 * 60


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


def reap_stale_locks(
    conn: sqlite3.Connection,
    max_age_seconds: float = DEFAULT_STALE_LOCK_SECONDS,
) -> list[int]:
    """Delete locks that cannot still represent an active writer/reader.

    A lock is stale when the owning job is no longer running, when the job row
    disappeared, or when an old lock was left by a dead process on this host.
    Running jobs with a live owner pid are kept even if they are long-lived.
    """
    rows = conn.execute(
        """
        SELECT locks.id, locks.job_id, locks.owner, locks.acquired_at, jobs.status
        FROM locks
        LEFT JOIN jobs ON jobs.id = locks.job_id
        """
    ).fetchall()
    stale_lock_ids: list[int] = []
    stale_job_ids: list[int] = []
    now = time.time()
    for row in rows:
        status = row["status"]
        job_id = row["job_id"]
        if status != "running":
            stale_lock_ids.append(int(row["id"]))
            if job_id is not None:
                stale_job_ids.append(int(job_id))
            continue
        age = now - _sqlite_timestamp(row["acquired_at"])
        if age >= max_age_seconds and not _owner_is_alive(row["owner"]):
            stale_lock_ids.append(int(row["id"]))
            if job_id is not None:
                stale_job_ids.append(int(job_id))
    if stale_lock_ids:
        with conn:
            conn.executemany("DELETE FROM locks WHERE id = ?", [(lock_id,) for lock_id in stale_lock_ids])
    return stale_job_ids


def _owner_is_alive(owner: str) -> bool:
    parts = owner.rsplit(":", 1)
    if len(parts) != 2:
        return True
    host, pid_text = parts
    if host != os.uname().nodename:
        return True
    try:
        pid = int(pid_text)
    except ValueError:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sqlite_timestamp(value: str) -> float:
    try:
        parsed = time.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return 0.0
    return calendar.timegm(parsed)
