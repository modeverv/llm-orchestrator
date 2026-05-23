from __future__ import annotations

import pytest

from fyws.db import connect, init_db


def test_connect_context_closes_after_outer_with(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    with connect(db) as conn:
        conn.execute("SELECT 1")
        with conn:
            conn.execute("SELECT 1")
        conn.execute("SELECT 1")

    with pytest.raises(Exception):
        conn.execute("SELECT 1")


def test_init_db_migrates_old_jobs_status_check_to_discarded(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    raw = connect(db)
    raw.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            prompt_path TEXT NOT NULL,
            cwd TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('read', 'write', 'deploy')),
            worker TEXT NOT NULL DEFAULT 'gemini',
            status TEXT NOT NULL CHECK (
                status IN ('queued', 'running', 'succeeded', 'failed', 'waiting_human')
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
        );
        CREATE TABLE job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO jobs(project, prompt_path, cwd, mode, worker, status, safe_score, c_score, o_score, i_score)
        VALUES ('p', 'task.md', '/tmp/p', 'write', 'gemini', 'queued', 0.5, 0.8, 0.8, 0.2);
        INSERT INTO job_events(job_id, event_type, message) VALUES (1, 'queued', 'queued p');
        """
    )
    raw.close()

    init_db(db)

    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO jobs(project, prompt_path, cwd, mode, worker, status, safe_score, c_score, o_score, i_score)
            VALUES ('p', 'task.md', '/tmp/p', 'write', 'gemini', 'discarded', 0.5, 0.8, 0.8, 0.2)
            """
        )
        fk_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'job_events'"
        ).fetchone()["sql"]
        count = conn.execute("SELECT COUNT(*) AS n FROM job_events WHERE job_id = 1").fetchone()
    assert "REFERENCES jobs" in fk_sql
    assert count["n"] == 1
