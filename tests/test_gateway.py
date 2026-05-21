from __future__ import annotations

import concurrent.futures
import subprocess
import sys
from pathlib import Path

from fyws.db import connect
from fyws.gateway import (
    discover_ownership_paths,
    format_gate,
    format_queued,
    parse_project_message,
    queue_from_message,
)


def test_parse_project_message():
    parsed = parse_project_message("spobook: FAQページのCSS、レスポンシブ対応して")
    assert parsed.project == "spobook"
    assert parsed.instruction == "FAQページのCSS、レスポンシブ対応して"
    assert parsed.worker == "gemini"


def test_parse_project_message_with_worker_prefix():
    parsed = parse_project_message("codex myproj1: fizzbuzzを実装して")
    assert parsed.worker == "codex"
    assert parsed.project == "myproj1"
    assert parsed.instruction == "fizzbuzzを実装して"


def test_parse_project_message_with_japanese_quotes():
    parsed = parse_project_message("「clientA: 検索結果ページの表示速度改善して」")
    assert parsed.project == "clientA"
    assert parsed.instruction == "検索結果ページの表示速度改善して"


def test_discover_ownership_paths_from_project_acceptance(tmp_path):
    (tmp_path / "ACCEPTANCE.md").write_text(
        """# Acceptance

```yaml
ownership:
  mode: write
  paths:
    - app/
    - tests/
```
""",
        encoding="utf-8",
    )
    assert discover_ownership_paths(tmp_path) == ["app/", "tests/"]


def test_queue_from_message_writes_task_and_acceptance(tmp_path):
    project = tmp_path / "spobook"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    queued = queue_from_message("spobook: fix css", work_root=tmp_path, db_path=tmp_path / "jobs.sqlite3")
    assert queued.project == "spobook"
    assert queued.worker == "gemini"
    assert queued.safe_score == 0.5120000000000001
    assert queued.task_path.read_text(encoding="utf-8") == "fix css\n"
    acceptance = queued.acceptance_path.read_text(encoding="utf-8")
    assert queued.acceptance_path.name == "task.acceptance.md"
    assert "safe = C x O x (1 - I): 0.512" in acceptance
    assert "mode: write" in acceptance
    assert format_queued(queued) == f"spobook #{queued.job_id} queued worker=gemini (safe=0.512)"


def test_queue_from_message_uses_worker_prefix(tmp_path):
    project = tmp_path / "myproj1"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    db = tmp_path / "jobs.sqlite3"

    queued = queue_from_message("codex myproj1: fizzbuzzを実装して", work_root=tmp_path, db_path=db)

    assert queued.worker == "codex"
    assert queued.project == "myproj1"
    assert queued.task_path.read_text(encoding="utf-8") == "fizzbuzzを実装して\n"
    with connect(db) as conn:
        row = conn.execute("SELECT worker FROM jobs WHERE id = ?", (queued.job_id,)).fetchone()
    assert row["worker"] == "codex"


def test_queue_from_message_explicit_worker_overrides_prefix(tmp_path):
    project = tmp_path / "myproj1"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    db = tmp_path / "jobs.sqlite3"

    queued = queue_from_message("codex myproj1: fix", work_root=tmp_path, worker="claude", db_path=db)

    assert queued.worker == "claude"
    with connect(db) as conn:
        row = conn.execute("SELECT worker FROM jobs WHERE id = ?", (queued.job_id,)).fetchone()
    assert row["worker"] == "claude"


def test_queue_from_message_does_not_overwrite_project_acceptance(tmp_path):
    project = tmp_path / "spobook"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules", encoding="utf-8")
    (project / "ACCEPTANCE.md").write_text("project defaults", encoding="utf-8")

    queued = queue_from_message("spobook: fix css", work_root=tmp_path, db_path=tmp_path / "jobs.sqlite3")

    assert queued.acceptance_path == project / "task.acceptance.md"
    assert (project / "ACCEPTANCE.md").read_text(encoding="utf-8") == "project defaults"


def test_queue_from_message_handles_parallel_db_init(tmp_path):
    for name in ("clientA", "clientB"):
        project = tmp_path / name
        project.mkdir()
        (project / "AGENTS.md").write_text("rules", encoding="utf-8")

    db = tmp_path / "jobs.sqlite3"
    messages = ["clientA: fix css", "clientB: fix search"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        queued = list(pool.map(lambda msg: queue_from_message(msg, work_root=tmp_path, db_path=db), messages))

    assert sorted(job.project for job in queued) == ["clientA", "clientB"]
    assert sorted(job.job_id for job in queued) == [1, 2]


def test_discord_helper_handles_parallel_process_db_init(tmp_path):
    for name in ("clientA", "clientB"):
        project = tmp_path / name
        project.mkdir()
        (project / "AGENTS.md").write_text("rules", encoding="utf-8")

    root = Path(__file__).resolve().parent.parent
    db = tmp_path / "jobs.sqlite3"
    commands = [
        [
            sys.executable,
            str(root / "discord_bot.py"),
            "--work-root",
            str(tmp_path),
            "--db",
            str(db),
            message,
        ]
        for message in ("clientA: fix css", "clientB: fix search")
    ]
    procs = [subprocess.Popen(cmd, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for cmd in commands]
    results = [proc.communicate(timeout=10) + (proc.returncode,) for proc in procs]

    assert all(returncode == 0 for _, _, returncode in results), results


def test_format_gate_message():
    assert format_gate(7, "clientA", "needs_review", "OK?") == "clientA #7 human_gate [needs_review]\nOK?"
