from __future__ import annotations

from fyws.db import connect, init_db
import os

from fyws.lock import acquire_lock, check_conflict, reap_stale_locks, release_lock


def _job(conn, mode: str = "write") -> int:
    cur = conn.execute(
        """
        INSERT INTO jobs(project, prompt_path, cwd, mode, worker, status, safe_score, c_score, o_score, i_score)
        VALUES ('p', 'task.md', '/tmp/p', ?, 'gemini', 'queued', 0.5, 0.8, 0.8, 0.2)
        """,
        (mode,),
    )
    return int(cur.lastrowid)


def test_write_lock_blocks_same_project_write(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    with connect(db) as conn:
        first = _job(conn)
        second = _job(conn)
        assert acquire_lock(conn, first, "p", "/tmp/p", "write")
        assert check_conflict(conn, "p", "/tmp/p", "write")
        assert not acquire_lock(conn, second, "p", "/tmp/p", "write")
        release_lock(conn, first)
        assert acquire_lock(conn, second, "p", "/tmp/p", "write")


def test_read_locks_can_share_when_no_writer(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    with connect(db) as conn:
        first = _job(conn, "read")
        second = _job(conn, "read")
        assert acquire_lock(conn, first, "p", "/tmp/p", "read")
        assert acquire_lock(conn, second, "p", "/tmp/p", "read")


def test_reap_stale_locks_removes_dead_owner_lock(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    with connect(db) as conn:
        job_id = _job(conn)
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
        conn.execute(
            """
            INSERT INTO locks(project, cwd, mode, job_id, owner, acquired_at)
            VALUES ('p', '/tmp/p', 'write', ?, ?, datetime('now', '-2 hours'))
            """,
            (job_id, f"{os.uname().nodename}:999999"),
        )

        assert reap_stale_locks(conn, max_age_seconds=1) == [job_id]
        assert not check_conflict(conn, "p", "/tmp/p", "write")
