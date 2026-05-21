from __future__ import annotations

from fyws.db import connect, init_db
from fyws.lock import acquire_lock, check_conflict, release_lock


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
