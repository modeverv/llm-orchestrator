from __future__ import annotations

from discord_bot import handle_message
from fyws.db import connect


def test_handle_message_queues_project_job(tmp_path):
    project = tmp_path / "clientA"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    response = handle_message("clientA: fix search", str(tmp_path), str(tmp_path / "jobs.sqlite3"))
    assert response.startswith("clientA #1 queued worker=gemini (safe=0.512)")


def test_handle_message_queues_worker_prefixed_job(tmp_path):
    project = tmp_path / "clientA"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    db = tmp_path / "jobs.sqlite3"

    response = handle_message("codex clientA: fix search", str(tmp_path), str(db))

    assert response.startswith("clientA #1 queued worker=codex (safe=0.512)")
    with connect(db) as conn:
        row = conn.execute("SELECT project, worker FROM jobs WHERE id = 1").fetchone()
    assert dict(row) == {"project": "clientA", "worker": "codex"}


def test_handle_message_status_empty(tmp_path):
    assert handle_message("status", str(tmp_path), str(tmp_path / "jobs.sqlite3")) == "no jobs"
