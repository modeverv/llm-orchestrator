from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import Callable

from .db import DEFAULT_DB_PATH, connect, init_db
from .lock import DEFAULT_STALE_LOCK_SECONDS, check_conflict, reap_stale_locks
from .orchestrator import run_job


Notifier = Callable[[int, str, str], None]


def queued_job_ids(db_path: str | Path = DEFAULT_DB_PATH, limit: int | None = None) -> list[int]:
    init_db(db_path)
    sql = "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at, id"
    params: tuple[int, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    with connect(db_path) as conn:
        return [int(row["id"]) for row in conn.execute(sql, params).fetchall()]


def run_once(
    db_path: str | Path = DEFAULT_DB_PATH,
    max_workers: int = 2,
    notifier: Notifier | None = None,
    worker_timeout_seconds: float | None = None,
    stale_lock_seconds: float = DEFAULT_STALE_LOCK_SECONDS,
) -> list[int]:
    init_db(db_path)
    with connect(db_path) as conn:
        stale_job_ids = reap_stale_locks(conn, stale_lock_seconds)
        for job_id in stale_job_ids:
            conn.execute(
                "INSERT INTO job_events(job_id, event_type, message, payload) VALUES (?, 'lock_reaped', ?, '{}')",
                (job_id, "stale lock reaped by runner"),
            )
        ids = _runnable_job_ids(conn, max_workers)
    if not ids:
        return []
    completed: list[int] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_id = {
            pool.submit(_run_job, job_id, db_path, worker_timeout_seconds): job_id
            for job_id in ids
        }
        for future in concurrent.futures.as_completed(future_to_id):
            job_id = future_to_id[future]
            future.result()
            completed.append(job_id)
            if notifier is not None:
                project, status = job_project_status(job_id, db_path)
                notifier(job_id, project, status)
    return completed


def run_forever(
    db_path: str | Path = DEFAULT_DB_PATH,
    max_workers: int = 2,
    interval_seconds: float = 5,
    notifier: Notifier | None = None,
    worker_timeout_seconds: float | None = None,
    stale_lock_seconds: float = DEFAULT_STALE_LOCK_SECONDS,
) -> None:
    from .orchestrator import recover_stuck_jobs

    recovered = recover_stuck_jobs(db_path)
    if recovered:
        print(f"recovered {len(recovered)} stuck jobs: {recovered}", flush=True)
    while True:
        try:
            run_once(
                db_path,
                max_workers=max_workers,
                notifier=notifier,
                worker_timeout_seconds=worker_timeout_seconds,
                stale_lock_seconds=stale_lock_seconds,
            )
        except Exception as exc:
            print(f"runner error (continuing): {exc}", flush=True)
        time.sleep(interval_seconds)


def job_project_status(job_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> tuple[str, str]:
    with connect(db_path) as conn:
        row = conn.execute("SELECT project, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return "", "missing"
    return row["project"], row["status"]


def _runnable_job_ids(conn, limit: int | None) -> list[int]:
    rows = conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at, id").fetchall()
    selected: list[int] = []
    virtual_locks: list[tuple[str, str, str]] = []
    for row in rows:
        if limit is not None and len(selected) >= limit:
            break
        if check_conflict(conn, row["project"], row["cwd"], row["mode"]):
            continue
        if _virtual_conflict(virtual_locks, row["project"], row["cwd"], row["mode"]):
            continue
        selected.append(int(row["id"]))
        virtual_locks.append((row["project"], row["cwd"], row["mode"]))
    return selected


def _virtual_conflict(locks: list[tuple[str, str, str]], project: str, cwd: str, mode: str) -> bool:
    modes = [lock_mode for lock_project, lock_cwd, lock_mode in locks if lock_project == project and lock_cwd == cwd]
    if mode == "read":
        return any(existing in ("write", "deploy") for existing in modes)
    return bool(modes)


def _run_job(job_id: int, db_path: str | Path, worker_timeout_seconds: float | None):
    if worker_timeout_seconds is None:
        return run_job(job_id, db_path)
    return run_job(job_id, db_path, None, worker_timeout_seconds)
