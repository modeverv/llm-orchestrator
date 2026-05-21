from __future__ import annotations

from discord_bot import format_job_notification
from fyws.db import connect, init_db
from fyws.gate import open_gate


def test_format_job_notification_includes_gate_question(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    with connect(db) as conn:
        job_id = conn.execute(
            """
            INSERT INTO jobs(project, prompt_path, cwd, mode, worker, status, safe_score, c_score, o_score, i_score)
            VALUES ('p', 'task.md', '/tmp/p', 'write', 'gemini', 'queued', 0.5, 0.8, 0.8, 0.2)
            """
        ).lastrowid
        open_gate(conn, job_id, "Approve?", "needs_review")
    assert format_job_notification(job_id, "p", "waiting_human", str(db)) == "p #1 human_gate [needs_review]\nApprove?"
