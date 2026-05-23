from __future__ import annotations

from pathlib import Path

import os

from fyws import runner
from fyws.db import connect, init_db
from fyws.orchestrator import queue_job


def test_queued_job_ids_ordered(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    first = queue_job("p", task, tmp_path, db_path=db)
    second = queue_job("p", task, tmp_path, db_path=db)
    assert runner.queued_job_ids(db) == [first, second]


def test_run_once_dispatches_and_notifies(tmp_path, monkeypatch):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, db_path=db)

    def fake_run_job(job_id_arg, db_path_arg):
        with connect(db_path_arg) as conn:
            conn.execute("UPDATE jobs SET status = 'succeeded' WHERE id = ?", (job_id_arg,))
        return None

    monkeypatch.setattr(runner, "run_job", fake_run_job)
    notifications = []
    completed = runner.run_once(db, max_workers=1, notifier=lambda jid, project, status: notifications.append((jid, project, status)))
    assert completed == [job_id]
    assert notifications == [(job_id, "p", "succeeded")]


def test_run_once_dispatches_parallel_reads_before_waiting_write(tmp_path, monkeypatch):
    db = tmp_path / "jobs.sqlite3"
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    first_read = queue_job("p", task, tmp_path, mode="read", db_path=db)
    second_read = queue_job("p", task, tmp_path, mode="read", db_path=db)
    write_job = queue_job("p", task, tmp_path, mode="write", db_path=db)
    seen = []

    def fake_run_job(job_id_arg, db_path_arg):
        seen.append(job_id_arg)
        with connect(db_path_arg) as conn:
            conn.execute("UPDATE jobs SET status = 'succeeded' WHERE id = ?", (job_id_arg,))
        return None

    monkeypatch.setattr(runner, "run_job", fake_run_job)

    completed = runner.run_once(db, max_workers=3)

    assert set(completed) == {first_read, second_read}
    assert set(seen) == {first_read, second_read}
    with connect(db) as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (write_job,)).fetchone()
        assert row["status"] == "queued"


def test_run_once_passes_auto_continue_flag(tmp_path, monkeypatch):
    db = tmp_path / "jobs.sqlite3"
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, db_path=db)
    calls = []

    def fake_run_job(job_id_arg, db_path_arg, worker_timeout_seconds=None, auto_continue_token_limit=False):
        calls.append((job_id_arg, worker_timeout_seconds, auto_continue_token_limit))
        with connect(db_path_arg) as conn:
            conn.execute("UPDATE jobs SET status = 'succeeded' WHERE id = ?", (job_id_arg,))
        return None

    monkeypatch.setattr(runner, "run_job", fake_run_job)

    completed = runner.run_once(db, max_workers=1, auto_continue_token_limit=True)

    assert completed == [job_id]
    assert calls == [(job_id, None, True)]


def test_run_once_requeues_dead_owner_running_job(tmp_path, monkeypatch):
    db = tmp_path / "jobs.sqlite3"
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    stuck = queue_job("p", task, tmp_path, db_path=db)
    waiting = queue_job("p", task, tmp_path, db_path=db)
    with connect(db) as conn:
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (stuck,))
        conn.execute(
            """
            INSERT INTO locks(project, cwd, mode, job_id, owner)
            VALUES ('p', ?, 'write', ?, ?)
            """,
            (str(tmp_path.resolve()), stuck, f"{os.uname().nodename}:999999"),
        )

    seen = []

    def fake_run_job(job_id_arg, db_path_arg):
        seen.append(job_id_arg)
        with connect(db_path_arg) as conn:
            conn.execute("UPDATE jobs SET status = 'succeeded' WHERE id = ?", (job_id_arg,))
        return None

    monkeypatch.setattr(runner, "run_job", fake_run_job)

    completed = runner.run_once(db, max_workers=1)

    assert completed == [stuck]
    assert seen == [stuck]
    with connect(db) as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (waiting,)).fetchone()
        event = conn.execute(
            "SELECT event_type FROM job_events WHERE job_id = ? AND event_type = 'lock_reaped'",
            (stuck,),
        ).fetchone()
    assert row["status"] == "queued"
    assert event is not None
