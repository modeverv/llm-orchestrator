from __future__ import annotations

import subprocess
from pathlib import Path

from fyws.db import connect, init_db
from fyws import orchestrator
from fyws.orchestrator import compute_safe, dry_run_check, queue_job, run_job, worker_requires_human
from fyws.workers.base import WorkerResult


class FakeWorker:
    def __init__(self, success: bool = True, message: str = "done", write_file: str | None = None) -> None:
        self.success = success
        self.message = message
        self.write_file = write_file

    def run(self, prompt_path, cwd, artifact_dir, ownership_paths, resume=False):
        events = Path(artifact_dir) / "events.jsonl"
        events.write_text('{"text":"done"}\n', encoding="utf-8")
        (Path(artifact_dir) / "last_message.txt").write_text(self.message, encoding="utf-8")
        if self.write_file:
            (Path(cwd) / self.write_file).write_text("changed", encoding="utf-8")
        return WorkerResult(self.success, self.message, str(events), step_count=1, error=None if self.success else "failed")


def test_compute_safe():
    assert compute_safe(0.8, 0.5, 0.25) == 0.30000000000000004


def test_queue_low_safe_opens_gate(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, c_score=0.2, o_score=0.5, i_score=0.5, db_path=db)
    with connect(db) as conn:
        job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        gates = conn.execute("SELECT COUNT(*) AS n FROM human_requests WHERE job_id = ?", (job_id,)).fetchone()
        assert job["status"] == "waiting_human"
        assert gates["n"] == 1


def test_run_job_with_fake_worker_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, ownership_paths=["owned.py"], db_path=db)
    result = run_job(job_id, db_path=db, worker_impl=FakeWorker())
    assert result.success
    with connect(db) as conn:
        job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        metrics = conn.execute("SELECT COUNT(*) AS n FROM job_metrics").fetchone()
        assert job["status"] == "succeeded"
        assert metrics["n"] == 1


def test_next_job_context_includes_previous_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    first = queue_job("p", task, tmp_path, db_path=db)
    run_job(first, db_path=db, worker_impl=FakeWorker())
    second = queue_job("p", task, tmp_path, db_path=db)
    run_job(second, db_path=db, worker_impl=FakeWorker())
    context = (orchestrator.ARTIFACTS_DIR / str(second) / "context.md").read_text(encoding="utf-8")
    assert "# previous summary.md" in context
    assert "# Job Summary" in context


def test_context_includes_site_context_and_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    (tmp_path / "SITE_CONTEXT.md").write_text("site facts", encoding="utf-8")
    (tmp_path / "ACCEPTANCE.md").write_text("acceptance facts", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, db_path=db)
    run_job(job_id, db_path=db, worker_impl=FakeWorker())
    context = (orchestrator.ARTIFACTS_DIR / str(job_id) / "context.md").read_text(encoding="utf-8")
    assert "# SITE_CONTEXT.md" in context
    assert "site facts" in context
    assert "# ACCEPTANCE.md" in context
    assert "acceptance facts" in context


def test_dry_run_check_reports_safe_and_lock(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    check = dry_run_check("p", tmp_path, "write", 0.2, 0.5, 0.5, db)
    assert check["safe_score"] == 0.05
    assert check["requires_human_gate"]
    assert not check["lock_conflict"]


def test_out_of_scope_change_opens_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    (tmp_path / "owned.py").write_text("owned", encoding="utf-8")
    (tmp_path / "other.py").write_text("original", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    job_id = queue_job("p", task, tmp_path, ownership_paths=["owned.py"], db_path=db)
    run_job(job_id, db_path=db, worker_impl=FakeWorker(write_file="other.py"))
    with connect(db) as conn:
        job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        gates = conn.execute("SELECT reason FROM human_requests WHERE job_id = ?", (job_id,)).fetchall()
        assert job["status"] == "waiting_human"
        assert [row["reason"] for row in gates] == ["out_of_scope_changes"]


def test_second_failure_opens_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, db_path=db)
    run_job(job_id, db_path=db, worker_impl=FakeWorker(success=False))
    run_job(job_id, db_path=db, worker_impl=FakeWorker(success=False))
    with connect(db) as conn:
        job = conn.execute("SELECT status, attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
        gate = conn.execute("SELECT reason FROM human_requests WHERE job_id = ?", (job_id,)).fetchone()
        assert job["status"] == "waiting_human"
        assert job["attempts"] == 2
        assert gate["reason"] == "two_consecutive_failures"


def test_worker_requested_human_opens_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, db_path=db)
    run_job(job_id, db_path=db, worker_impl=FakeWorker(message="判断が必要です"))
    with connect(db) as conn:
        job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        gate = conn.execute("SELECT reason FROM human_requests WHERE job_id = ?", (job_id,)).fetchone()
        assert job["status"] == "waiting_human"
        assert gate["reason"] == "worker_requested_human"


def test_worker_requires_human_markers():
    assert worker_requires_human("this requires human review")
    assert not worker_requires_human("all done")


def test_dot_ownership_allows_any_changed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    (tmp_path / "other.py").write_text("original", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    job_id = queue_job("p", task, tmp_path, ownership_paths=["."], db_path=db)
    run_job(job_id, db_path=db, worker_impl=FakeWorker(write_file="other.py"))
    with connect(db) as conn:
        job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert job["status"] == "succeeded"
