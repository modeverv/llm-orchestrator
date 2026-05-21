from __future__ import annotations

import subprocess

from fyws.workers.codex import CodexWorker, _extract_message


def test_codex_worker_runs_noninteractively(tmp_path, monkeypatch):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")
    artifact = tmp_path / "artifacts"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        last_path = artifact / "last_message.txt"
        last_path.parent.mkdir(parents=True, exist_ok=True)
        last_path.write_text("done", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"msg":"event"}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CodexWorker().run(prompt, tmp_path, artifact, ["notes.txt"])

    assert result.success
    assert result.last_message == "done"
    assert calls[0][0] == [
        "codex",
        "exec",
        "-C",
        str(tmp_path.resolve()),
        "--json",
        "--output-last-message",
        str(artifact / "last_message.txt"),
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    ]
    assert calls[0][1]["input"] == "do it"


def test_codex_worker_reports_missing_executable(tmp_path):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")

    result = CodexWorker(executable="missing-codex").run(prompt, tmp_path, tmp_path / "artifacts", [])

    assert not result.success
    assert "codex executable not found" in (result.error or "")


def test_extract_codex_nested_message():
    assert _extract_message({"item": {"message": "done"}}) == "done"
