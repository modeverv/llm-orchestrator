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

    def run(self, prompt_path, cwd, artifact_dir, ownership_paths, resume=False, timeout_seconds=None):
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


def test_queue_job_reads_project_acceptance_defaults(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "ACCEPTANCE.md").write_text(
        """# Acceptance

## safe(T) Score

- C: 0.6
- O: 0.5
- I: 0.2

```yaml
ownership:
  mode: read
  paths:
    - src/
```
""",
        encoding="utf-8",
    )

    job_id = queue_job("p", task, tmp_path, db_path=db)

    with connect(db) as conn:
        job = conn.execute("SELECT mode, safe_score, c_score, o_score, i_score, ownership_paths FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job["mode"] == "read"
    assert job["safe_score"] == 0.24
    assert job["c_score"] == 0.6
    assert job["o_score"] == 0.5
    assert job["i_score"] == 0.2
    assert job["ownership_paths"] == '["src/"]'


def test_deploy_mode_opens_gate_even_when_safe_is_high(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    task = tmp_path / "task.md"
    task.write_text("ship it", encoding="utf-8")

    job_id = queue_job("p", task, tmp_path, mode="deploy", c_score=1, o_score=1, i_score=0, db_path=db)

    with connect(db) as conn:
        job = conn.execute("SELECT status, safe_score FROM jobs WHERE id = ?", (job_id,)).fetchone()
        gate_row = conn.execute("SELECT reason FROM human_requests WHERE job_id = ?", (job_id,)).fetchone()
    assert job["safe_score"] == 1
    assert job["status"] == "waiting_human"
    assert gate_row["reason"] == "deploy_requires_human"


def test_secret_operation_opens_gate_even_when_safe_is_high(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    task = tmp_path / "task.md"
    task.write_text("rotate the API key used by CI", encoding="utf-8")

    job_id = queue_job("p", task, tmp_path, c_score=1, o_score=1, i_score=0, db_path=db)

    with connect(db) as conn:
        job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        gate_row = conn.execute("SELECT reason FROM human_requests WHERE job_id = ?", (job_id,)).fetchone()
    assert job["status"] == "waiting_human"
    assert gate_row["reason"] == "secret_operation_requires_human"


def test_queue_job_selects_active_project_template_then_default(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    with connect(db) as conn:
        default_id = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES ('default', 1, 'active', 'default')"
        ).lastrowid
        project_id = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES ('p', 1, 'active', 'project')"
        ).lastrowid
        conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES ('p', 2, 'draft', 'draft')"
        )

    p_job = queue_job("p", task, tmp_path, db_path=db)
    other_job = queue_job("other", task, tmp_path, db_path=db)

    with connect(db) as conn:
        selected = {
            row["id"]: row["prompt_template_id"]
            for row in conn.execute("SELECT id, prompt_template_id FROM jobs WHERE id IN (?, ?)", (p_job, other_job))
        }
    assert selected[p_job] == project_id
    assert selected[other_job] == default_id


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


def test_run_job_summary_includes_changed_files_commands_and_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    (tmp_path / "ACCEPTANCE.md").write_text(
        "## Verify Commands\n\n- python -c \"print('verified')\"\n",
        encoding="utf-8",
    )
    (tmp_path / "owned.py").write_text("original", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    job_id = queue_job("p", task, tmp_path, ownership_paths=["owned.py"], db_path=db)
    run_job(job_id, db_path=db, worker_impl=FakeWorker(message="Decisions Made:\n- edited owned file\nNext Action:\n- ship it", write_file="owned.py"))

    summary = (orchestrator.ARTIFACTS_DIR / str(job_id) / "summary.md").read_text(encoding="utf-8")
    assert "## Files Changed\n- owned.py" in summary
    assert "## Verification\n- $ python -c \"print('verified')\"" in summary
    assert "## Decisions Made\n- edited owned file" in summary
    assert "queued: queued p safe=0.512" in summary
    assert "succeeded: job completed" in summary
    assert "## Next Action\n- ship it" in summary
    assert (orchestrator.ARTIFACTS_DIR / str(job_id) / "diff.patch").exists()


def test_token_limit_opens_gate_and_records_summary_blocker(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, db_path=db)

    run_job(job_id, db_path=db, worker_impl=FakeWorker(message="Reached maximum context length."))

    with connect(db) as conn:
        job = conn.execute("SELECT status, last_error FROM jobs WHERE id = ?", (job_id,)).fetchone()
        continuation = conn.execute("SELECT * FROM jobs WHERE id != ?", (job_id,)).fetchone()
        gate_row = conn.execute("SELECT reason FROM human_requests WHERE job_id = ?", (continuation["id"],)).fetchone()
    summary = (orchestrator.ARTIFACTS_DIR / str(job_id) / "summary.md").read_text(encoding="utf-8")
    context = (orchestrator.ARTIFACTS_DIR / str(job_id) / "context.md").read_text(encoding="utf-8")
    assert job["status"] == "failed"
    assert job["last_error"] == "token limit reached; continuation job queued"
    assert continuation["status"] == "waiting_human"
    assert continuation["prompt_path"].endswith("continue_prompt.md")
    assert "## Blockers\n- token_limit_reached" in summary
    assert "# previous summary.md" in context


def test_run_job_clears_stale_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, ownership_paths=["owned.py"], db_path=db)
    artifact_dir = orchestrator.ARTIFACTS_DIR / str(job_id)
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "summary.md").write_text("stale summary", encoding="utf-8")
    (artifact_dir / "diff.patch").write_text("stale diff", encoding="utf-8")

    run_job(job_id, db_path=db, worker_impl=FakeWorker())

    assert "stale summary" not in (artifact_dir / "summary.md").read_text(encoding="utf-8")
    assert not (artifact_dir / "diff.patch").exists()


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
    assert "# ACCEPTANCE.md (project default)" in context
    assert "acceptance facts" in context


def test_context_prefers_job_specific_acceptance_over_project_default(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    task_dir = tmp_path / "tasks"
    task_dir.mkdir()
    task = task_dir / "task.md"
    task.write_text("do it", encoding="utf-8")
    (task_dir / "task.acceptance.md").write_text("job acceptance", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    (tmp_path / "ACCEPTANCE.md").write_text("project acceptance", encoding="utf-8")

    job_id = queue_job("p", task, tmp_path, db_path=db)
    run_job(job_id, db_path=db, worker_impl=FakeWorker())

    context = (orchestrator.ARTIFACTS_DIR / str(job_id) / "context.md").read_text(encoding="utf-8")
    assert "# task.acceptance.md (job-specific)" in context
    assert "job acceptance" in context
    assert "project acceptance" not in context


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


def test_untracked_out_of_scope_change_opens_gate(tmp_path, monkeypatch):
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
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    job_id = queue_job("p", task, tmp_path, ownership_paths=["owned.py"], db_path=db)

    run_job(job_id, db_path=db, worker_impl=FakeWorker(write_file="new.py"))

    with connect(db) as conn:
        job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        gate_row = conn.execute("SELECT reason FROM human_requests WHERE job_id = ?", (job_id,)).fetchone()
    assert job["status"] == "waiting_human"
    assert gate_row["reason"] == "out_of_scope_changes"


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
    assert worker_requires_human("I cannot proceed without human approval.")
    assert worker_requires_human("Please confirm before I change the billing flow.")
    assert worker_requires_human("人間の確認が必要です")
    assert not worker_requires_human("all done")


def test_route_worker_supports_codex():
    assert orchestrator.route_worker("codex").__class__.__name__ == "CodexWorker"


def test_inspect_job_shows_db_artifacts_summary_diff_and_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    job_id = queue_job("p", task, tmp_path, c_score=0.1, o_score=0.5, i_score=0.5, db_path=db)
    artifact = orchestrator.ARTIFACTS_DIR / str(job_id)
    artifact.mkdir(parents=True)
    (artifact / "summary.md").write_text("# Job Summary\n\n## Current State\nwaiting", encoding="utf-8")
    (artifact / "diff.patch").write_text("diff --git a/x b/x\n", encoding="utf-8")

    report = orchestrator.inspect_job(job_id, db)

    assert "# Job 1" in report
    assert "status: waiting_human" in report
    assert "safe_score: 0.025" in report
    assert "summary.md" in report
    assert "## Gate" in report
    assert "safe_below_threshold" in report
    assert "diff --git a/x b/x" in report


def test_job_log_text_falls_back_to_events_then_last_message(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    artifact = orchestrator.ARTIFACTS_DIR / "9"
    artifact.mkdir(parents=True)
    (artifact / "events.jsonl").write_text('{"message":"event"}\n', encoding="utf-8")
    (artifact / "last_message.txt").write_text("last", encoding="utf-8")

    assert orchestrator.job_log_text(9) == '{"message":"event"}\n'
    (artifact / "events.jsonl").unlink()
    assert orchestrator.job_log_text(9) == "last"


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


def test_retry_context_includes_previous_diff_patch(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    (tmp_path / "owned.py").write_text("original", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    first = queue_job("p", task, tmp_path, ownership_paths=["owned.py"], db_path=db)
    run_job(first, db_path=db, worker_impl=FakeWorker(write_file="owned.py"))
    retry = orchestrator.retry_job(first, db)

    run_job(retry, db_path=db, worker_impl=FakeWorker())

    context = (orchestrator.ARTIFACTS_DIR / str(retry) / "context.md").read_text(encoding="utf-8")
    assert "# diff.patch" in context
    assert "owned.py" in context


def test_prune_artifacts_deletes_old_completed_jobs_only(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ARTIFACTS_DIR", tmp_path / "artifacts")
    db = tmp_path / "jobs.sqlite3"
    init_db(db)
    task = tmp_path / "task.md"
    task.write_text("do it", encoding="utf-8")
    old_job = queue_job("old", task, tmp_path, db_path=db)
    queued_job = queue_job("queued", task, tmp_path, db_path=db)
    recent_job = queue_job("recent", task, tmp_path, db_path=db)
    for job_id in (old_job, queued_job, recent_job):
        artifact = orchestrator.ARTIFACTS_DIR / str(job_id)
        artifact.mkdir(parents=True)
        (artifact / "summary.md").write_text("summary", encoding="utf-8")
    with connect(db) as conn:
        conn.execute("UPDATE jobs SET status = 'succeeded', finished_at = datetime('now', '-10 days') WHERE id = ?", (old_job,))
        conn.execute("UPDATE jobs SET status = 'queued', updated_at = datetime('now', '-10 days') WHERE id = ?", (queued_job,))
        conn.execute("UPDATE jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (recent_job,))

    dry_targets = orchestrator.prune_artifacts(keep_days=7, db_path=db, dry_run=True)
    targets = orchestrator.prune_artifacts(keep_days=7, db_path=db)

    assert dry_targets == [orchestrator.ARTIFACTS_DIR / str(old_job)]
    assert targets == [orchestrator.ARTIFACTS_DIR / str(old_job)]
    assert not (orchestrator.ARTIFACTS_DIR / str(old_job)).exists()
    assert (orchestrator.ARTIFACTS_DIR / str(queued_job)).exists()
    assert (orchestrator.ARTIFACTS_DIR / str(recent_job)).exists()
