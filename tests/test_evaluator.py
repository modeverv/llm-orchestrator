from __future__ import annotations

from fyws.db import connect, init_db
from fyws.evaluator import approve_template, propose_improvement


def test_propose_improvement_creates_draft_only_after_minimum_samples(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    init_db(db)
    prompts: list[str] = []

    def fake_proposer(prompt: str) -> str:
        prompts.append(prompt)
        return "improved body from metrics"

    with connect(db) as conn:
        template_id = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES ('default', 1, 'active', 'body')"
        ).lastrowid
        assert propose_improvement(conn, template_id, minimum_samples=1, proposer=fake_proposer, artifacts_dir=artifact_dir) is None
        job_id = conn.execute(
            """
            INSERT INTO jobs(project, prompt_path, cwd, mode, worker, status, safe_score, c_score, o_score, i_score, prompt_template_id)
            VALUES ('p', 'task.md', '/tmp/p', 'write', 'gemini', 'failed', 0.5, 0.8, 0.8, 0.2, ?)
            """,
            (template_id,),
        ).lastrowid
        summary = artifact_dir / str(job_id) / "summary.md"
        summary.parent.mkdir(parents=True)
        summary.write_text("# Job Summary\n\n## Blockers\nverification failed", encoding="utf-8")
        conn.execute(
            "INSERT INTO job_metrics(job_id, prompt_template_id, worker, outcome, duration_seconds) VALUES (?, ?, 'gemini', 'failed', 1)",
            (job_id, template_id),
        )
        draft_id = propose_improvement(conn, template_id, minimum_samples=1, proposer=fake_proposer, artifacts_dir=artifact_dir)
        draft = conn.execute("SELECT status, version, body FROM prompt_templates WHERE id = ?", (draft_id,)).fetchone()
        assert draft["status"] == "draft"
        assert draft["version"] == 2
        assert draft["body"] == "improved body from metrics"

    assert "Non-success outcomes: 1" in prompts[0]
    assert "verification failed" in prompts[0]


def test_approve_template_deprecates_previous_active_for_same_name(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    with connect(db) as conn:
        active_id = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES ('default', 1, 'active', 'v1')"
        ).lastrowid
        draft_id = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES ('default', 2, 'draft', 'v2')"
        ).lastrowid
        other_active_id = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES ('project-a', 1, 'active', 'project')"
        ).lastrowid

        approve_template(conn, draft_id)

        rows = {
            row["id"]: row["status"]
            for row in conn.execute("SELECT id, status FROM prompt_templates WHERE id IN (?, ?, ?)", (active_id, draft_id, other_active_id))
        }
        assert rows[active_id] == "deprecated"
        assert rows[draft_id] == "active"
        assert rows[other_active_id] == "active"
