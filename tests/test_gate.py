from __future__ import annotations

from fyws.db import connect, init_db
from fyws.gate import answer_gate, list_open_gates, open_gate


def test_open_and_answer_gate_requeues_job(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    with connect(db) as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs(project, prompt_path, cwd, mode, worker, status, safe_score, c_score, o_score, i_score)
            VALUES ('p', 'task.md', '/tmp/p', 'write', 'gemini', 'queued', 0.2, 0.5, 0.5, 0.2)
            """
        )
        job_id = int(cur.lastrowid)
        open_gate(conn, job_id, "approve?", "safe_below_threshold")
        assert len(list_open_gates(conn)) == 1
        answer_gate(conn, job_id, "yes")
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "queued"
        assert list_open_gates(conn) == []
