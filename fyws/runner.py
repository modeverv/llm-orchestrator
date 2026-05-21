from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import Callable

from .db import DEFAULT_DB_PATH, connect, init_db
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
) -> list[int]:
    ids = queued_job_ids(db_path, max_workers)
    if not ids:
        return []
    completed: list[int] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_id = {pool.submit(run_job, job_id, db_path): job_id for job_id in ids}
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
) -> None:
    from .orchestrator import recover_stuck_jobs

    recovered = recover_stuck_jobs(db_path)
    if recovered:
        print(f"recovered {len(recovered)} stuck jobs: {recovered}", flush=True)
    while True:
        try:
            run_once(db_path, max_workers=max_workers, notifier=notifier)
        except Exception as exc:
            print(f"runner error (continuing): {exc}", flush=True)
        time.sleep(interval_seconds)


def job_project_status(job_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> tuple[str, str]:
    with connect(db_path) as conn:
        row = conn.execute("SELECT project, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return "", "missing"
    return row["project"], row["status"]
