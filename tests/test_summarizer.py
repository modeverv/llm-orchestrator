from __future__ import annotations

from fyws import summarizer


def test_summarize_uses_agents_non_negotiable_rules(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        """# Project Rules

## Non-Negotiable Rules

1. Use SQLite for state.
2. Never write without a lock.

## Other

- Later text
""",
        encoding="utf-8",
    )

    summary_path = summarizer.summarize(
        artifact,
        "do it",
        str(tmp_path),
        "succeeded",
        "done",
        agents_path=agents,
    )

    summary = summary_path.read_text(encoding="utf-8")
    assert "## Non-Negotiable Rules\n- Use SQLite for state.\n- Never write without a lock." in summary
    assert "Later text" not in summary


def test_summarize_falls_back_to_first_agents_line(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    agents = tmp_path / "AGENTS.md"
    agents.write_text("\nProject-specific first line\n\nmore", encoding="utf-8")

    summary_path = summarizer.summarize(
        artifact,
        "do it",
        str(tmp_path),
        "succeeded",
        "done",
        agents_path=agents,
    )

    assert "## Non-Negotiable Rules\n- Project-specific first line" in summary_path.read_text(encoding="utf-8")


def test_commands_from_events_reads_structured_command_events(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"event_type":"text","message":"hello"}',
                '{"event_type":"command","command":"pytest -q"}',
                '{"event_type":"command","command":"pytest -q"}',
                '{"event_type":"command","argv":["python","-m","py_compile","cli.py"]}',
                "not json",
            ]
        ),
        encoding="utf-8",
    )

    assert summarizer._commands_from_events(events) == ["pytest -q", "python -m py_compile cli.py"]


def test_summarize_includes_round_tripped_command_events(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "events.jsonl").write_text(
        '{"event_type":"command","command":"pytest -q"}\n{"event_type":"text","message":"done"}\n',
        encoding="utf-8",
    )

    summary_path = summarizer.summarize(artifact, "do it", str(tmp_path), "succeeded", "done")

    assert "## Commands Run\n- pytest -q" in summary_path.read_text(encoding="utf-8")


def test_extract_decisions_and_next_action_with_headings():
    decisions, next_action = summarizer._extract_decisions_and_next_action(
        "## 変更内容\n- ファイルを更新\n\n## 次アクション\n- テストする"
    )

    assert decisions == ["ファイルを更新"]
    assert next_action == ["テストする"]


def test_extract_decisions_and_next_action_falls_back_to_first_line():
    decisions, next_action = summarizer._extract_decisions_and_next_action("done\nmore details")

    assert decisions == ["done"]
    assert next_action == ["Review last_message.txt for the worker's full final note."]


def test_extract_decisions_and_next_action_empty_message():
    assert summarizer._extract_decisions_and_next_action("") == ([], [])


def test_verification_lines_distinguishes_none_from_empty():
    assert summarizer._verification_lines(None) == []
    assert summarizer._verification_lines([]) == ["No verify commands were configured."]
