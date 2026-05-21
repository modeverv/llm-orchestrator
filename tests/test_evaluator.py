from __future__ import annotations

from fyws.db import connect, init_db
from fyws.evaluator import propose_improvement


def test_propose_improvement_creates_draft_only_after_minimum_samples(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    with connect(db) as conn:
        template_id = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES ('default', 1, 'active', 'body')"
        ).lastrowid
        assert propose_improvement(conn, template_id, minimum_samples=1) is None
        job_id = conn.execute(
            """
            INSERT INTO jobs(project, prompt_path, cwd, mode, worker, status, safe_score, c_score, o_score, i_score, prompt_template_id)
            VALUES ('p', 'task.md', '/tmp/p', 'write', 'gemini', 'succeeded', 0.5, 0.8, 0.8, 0.2, ?)
            """,
            (template_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO job_metrics(job_id, prompt_template_id, worker, outcome, duration_seconds) VALUES (?, ?, 'gemini', 'succeeded', 1)",
            (job_id, template_id),
        )
        draft_id = propose_improvement(conn, template_id, minimum_samples=1)
        draft = conn.execute("SELECT status, version FROM prompt_templates WHERE id = ?", (draft_id,)).fetchone()
        assert draft["status"] == "draft"
        assert draft["version"] == 2
