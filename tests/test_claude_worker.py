from __future__ import annotations

from fyws.workers.claude import ClaudeWorker


def test_claude_worker_allows_noninteractive_edits(tmp_path, monkeypatch):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")
    artifact = tmp_path / "artifacts"
    executable = tmp_path / "fake-claude"
    argv_path = tmp_path / "argv.txt"
    stdin_path = tmp_path / "stdin.txt"
    executable.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$@" > {argv_path}
cat > {stdin_path}
printf '%s\\n' '{{"usage": {{"input_tokens": 7, "output_tokens": 3}}}}'
printf '%s\\n' done
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = ClaudeWorker(str(executable)).run(prompt, tmp_path, artifact, ["notes.txt"])

    assert result.success
    assert argv_path.read_text(encoding="utf-8").splitlines() == ["--print", "--permission-mode", "acceptEdits"]
    assert stdin_path.read_text(encoding="utf-8") == "do it"
    assert result.tokens_in == 7
    assert result.tokens_out == 3


def test_claude_worker_records_tool_use_commands(tmp_path):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")
    artifact = tmp_path / "artifacts"
    executable = tmp_path / "fake-claude"
    executable.write_text(
        """#!/bin/sh
cat > /dev/null
printf '%s\\n' '{"type":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"pytest -q"}}]}'
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = ClaudeWorker(str(executable)).run(prompt, tmp_path, artifact, [])

    assert result.success
    assert '{"event_type": "command", "command": "pytest -q"}' in (artifact / "events.jsonl").read_text(encoding="utf-8")


def test_claude_worker_times_out_and_writes_error(tmp_path):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")
    artifact = tmp_path / "artifacts"
    executable = tmp_path / "slow-claude"
    executable.write_text(
        """#!/bin/sh
sleep 5
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = ClaudeWorker(str(executable)).run(prompt, tmp_path, artifact, [], timeout_seconds=0.2)

    assert not result.success
    assert "timed out" in (result.error or "")
    assert "timed out" in (artifact / "events.jsonl").read_text(encoding="utf-8")
