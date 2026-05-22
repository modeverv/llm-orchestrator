from __future__ import annotations

from fyws.workers.codex import CodexWorker, _extract_message


def test_codex_worker_runs_noninteractively(tmp_path):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")
    artifact = tmp_path / "artifacts"
    executable = tmp_path / "fake-codex"
    argv_path = tmp_path / "argv.txt"
    stdin_path = tmp_path / "stdin.txt"
    executable.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$@" > {argv_path}
cat > {stdin_path}
while [ "$1" ]; do
  if [ "$1" = "--output-last-message" ]; then
    shift
    mkdir -p "$(dirname "$1")"
    printf '%s' done > "$1"
  fi
  shift
done
printf '%s\\n' '{{"msg":"event"}}'
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = CodexWorker(str(executable)).run(prompt, tmp_path, artifact, ["notes.txt"])

    assert result.success
    assert result.last_message == "done"
    assert argv_path.read_text(encoding="utf-8").splitlines() == [
        "exec",
        "-C",
        str(tmp_path.resolve()),
        "--json",
        "--output-last-message",
        str(artifact / "last_message.txt"),
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    ]
    assert stdin_path.read_text(encoding="utf-8") == "do it"


def test_codex_worker_records_json_command_events(tmp_path):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")
    artifact = tmp_path / "artifacts"
    executable = tmp_path / "fake-codex"
    executable.write_text(
        """#!/bin/sh
while [ "$1" ]; do
  if [ "$1" = "--output-last-message" ]; then
    shift
    mkdir -p "$(dirname "$1")"
    printf '%s' done > "$1"
  fi
  shift
done
cat > /dev/null
printf '%s\\n' '{"type":"tool_call","input":{"cmd":"python -m pytest -q"}}'
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = CodexWorker(str(executable)).run(prompt, tmp_path, artifact, [])

    assert result.success
    assert '{"event_type": "command", "command": "python -m pytest -q"}' in (artifact / "events.jsonl").read_text(
        encoding="utf-8"
    )


def test_codex_worker_reports_missing_executable(tmp_path):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")

    result = CodexWorker(executable="missing-codex").run(prompt, tmp_path, tmp_path / "artifacts", [])

    assert not result.success
    assert "codex executable not found" in (result.error or "")


def test_extract_codex_nested_message():
    assert _extract_message({"item": {"message": "done"}}) == "done"
