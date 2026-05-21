from __future__ import annotations

from discord_bot import handle_message


def test_handle_message_queues_project_job(tmp_path):
    project = tmp_path / "clientA"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    response = handle_message("clientA: fix search", str(tmp_path), str(tmp_path / "jobs.sqlite3"))
    assert response.startswith("clientA #1 queued (safe=0.512)")


def test_handle_message_status_empty(tmp_path):
    assert handle_message("status", str(tmp_path), str(tmp_path / "jobs.sqlite3")) == "no jobs"
