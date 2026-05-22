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
