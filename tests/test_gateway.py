from __future__ import annotations

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
    assert queued.safe_score == 0.5120000000000001
    assert queued.task_path.read_text(encoding="utf-8") == "fix css\n"
    acceptance = queued.acceptance_path.read_text(encoding="utf-8")
    assert "safe = C x O x (1 - I): 0.512" in acceptance
    assert "mode: write" in acceptance
    assert format_queued(queued) == f"spobook #{queued.job_id} queued (safe=0.512)"


def test_format_gate_message():
    assert format_gate(7, "clientA", "needs_review", "OK?") == "clientA #7 human_gate [needs_review]\nOK?"
