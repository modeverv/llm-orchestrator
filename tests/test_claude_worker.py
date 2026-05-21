from __future__ import annotations

import subprocess

from fyws.workers.claude import ClaudeWorker


def test_claude_worker_allows_noninteractive_edits(tmp_path, monkeypatch):
    prompt = tmp_path / "task.md"
    prompt.write_text("do it", encoding="utf-8")
    artifact = tmp_path / "artifacts"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ClaudeWorker().run(prompt, tmp_path, artifact, ["notes.txt"])

    assert result.success
    assert calls[0][0] == ["claude", "--print", "--permission-mode", "acceptEdits"]
    assert calls[0][1]["input"] == "do it"
