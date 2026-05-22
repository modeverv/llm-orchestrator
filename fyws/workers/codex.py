from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

from .base import WorkerResult


class CodexWorker:
    def __init__(self, executable: str = "codex", model: str | None = None) -> None:
        self.executable = executable
        self.model = model

    def run(
        self,
        prompt_path: str | Path,
        cwd: str | Path,
        artifact_dir: str | Path,
        ownership_paths: list[str],
        resume: bool = False,
        timeout_seconds: float | None = None,
    ) -> WorkerResult:
        prompt = Path(prompt_path).read_text(encoding="utf-8")
        artifact = Path(artifact_dir)
        artifact.mkdir(parents=True, exist_ok=True)
        events_path = artifact / "events.jsonl"
        last_path = artifact / "last_message.txt"

        cmd = [
            self.executable,
            "exec",
            "-C",
            str(Path(cwd).resolve()),
            "--json",
            "--output-last-message",
            str(last_path),
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append("-")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(prompt, timeout=timeout_seconds)
        except FileNotFoundError as exc:
            error = f"codex executable not found: {exc}"
            events_path.write_text(json.dumps({"event_type": "error", "message": error}) + "\n", encoding="utf-8")
            return WorkerResult(False, "", str(events_path), error=error)
        except subprocess.TimeoutExpired as exc:
            _terminate_process(proc)
            stdout = _as_text(exc.stdout)
            stderr = _as_text(exc.stderr)
            output = stdout + ("\n" if stdout and stderr else "") + stderr
            events_path.write_text(output + json.dumps({"event_type": "error", "message": f"codex timed out after {timeout_seconds:g}s"}) + "\n", encoding="utf-8")
            last_message = _last_message(last_path, output)
            last_path.write_text(last_message, encoding="utf-8")
            return WorkerResult(False, last_message, str(events_path), step_count=len(output.splitlines()), error=f"codex timed out after {timeout_seconds:g}s")

        output = stdout
        if stderr:
            output = output + ("\n" if output else "") + stderr
        events_path.write_text(output, encoding="utf-8")
        last_message = _last_message(last_path, output)
        last_path.write_text(last_message, encoding="utf-8")
        success = proc.returncode == 0
        return WorkerResult(
            success=success,
            last_message=last_message,
            events_path=str(events_path),
            step_count=len(output.splitlines()),
            error=None if success else f"codex exited with code {proc.returncode}",
        )


def _last_message(last_path: Path, output: str) -> str:
    if last_path.exists():
        message = last_path.read_text(encoding="utf-8", errors="replace").strip()
        if message:
            return message
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        parsed = _parse_json_line(line)
        if parsed is None:
            return line
        message = _extract_message(parsed)
        if message:
            return message
    return ""


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _terminate_process(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def _parse_json_line(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _extract_message(event: dict) -> str:
    for key in ("message", "text", "content", "last_message"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    item = event.get("item")
    if isinstance(item, dict):
        return _extract_message(item)
    return ""
