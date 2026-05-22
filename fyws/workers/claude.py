from __future__ import annotations

import json
import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path

from .base import WorkerResult


class ClaudeWorker:
    def __init__(self, executable: str = "claude") -> None:
        self.executable = executable

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
        last_message = ""
        step_count = 0
        tokens_in: int | None = None
        tokens_out: int | None = None

        try:
            proc = subprocess.Popen(
                [self.executable, "--print", "--permission-mode", "acceptEdits"],
                cwd=str(cwd),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            error = f"claude executable not found: {exc}"
            events_path.write_text(error + "\n", encoding="utf-8")
            return WorkerResult(False, "", str(events_path), error=error)

        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
        start = time.monotonic()
        with events_path.open("w", encoding="utf-8") as events:
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            while proc.poll() is None:
                if timeout_seconds is not None and time.monotonic() - start >= timeout_seconds:
                    _terminate_process(proc)
                    error = f"claude timed out after {timeout_seconds:g}s"
                    events.write(json.dumps({"event_type": "error", "message": error}) + "\n")
                    events.flush()
                    (artifact / "last_message.txt").write_text(last_message, encoding="utf-8")
                    return WorkerResult(False, last_message, str(events_path), tokens_in, tokens_out, step_count, error=error)
                for _key, _mask in selector.select(timeout=0.1):
                    line = proc.stdout.readline()
                    if line:
                        last_message, tokens_in, tokens_out, step_count = _record_line(
                            events, line, last_message, tokens_in, tokens_out, step_count
                        )
            for line in proc.stdout:
                if line:
                    last_message, tokens_in, tokens_out, step_count = _record_line(
                        events, line, last_message, tokens_in, tokens_out, step_count
                    )
            returncode = proc.wait()

        (artifact / "last_message.txt").write_text(last_message, encoding="utf-8")
        success = returncode == 0
        return WorkerResult(
            success=success,
            last_message=last_message,
            events_path=str(events_path),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            step_count=step_count,
            error=None if success else f"claude exited with code {returncode}",
        )


def _record_line(events, line: str, last_message: str, tokens_in: int | None, tokens_out: int | None, step_count: int):
    events.write(line)
    events.flush()
    step_count += 1
    if line.strip():
        last_message = line.strip()
    tokens_in, tokens_out = _extract_usage(line, tokens_in, tokens_out)
    return last_message, tokens_in, tokens_out, step_count


def _extract_usage(line: str, tokens_in: int | None, tokens_out: int | None) -> tuple[int | None, int | None]:
    parsed = _parse_json_line(line)
    if parsed is not None:
        usage = parsed.get("usage") or parsed.get("usageMetadata") or {}
        if isinstance(usage, dict):
            tokens_in = _first_int(usage, ["input_tokens", "prompt_tokens", "promptTokenCount"], tokens_in)
            tokens_out = _first_int(usage, ["output_tokens", "completion_tokens", "candidatesTokenCount"], tokens_out)
    text = line.lower()
    input_match = re.search(r"(?:input|prompt)[ _-]?tokens?\D+(\d+)", text)
    output_match = re.search(r"(?:output|completion)[ _-]?tokens?\D+(\d+)", text)
    if input_match:
        tokens_in = int(input_match.group(1))
    if output_match:
        tokens_out = int(output_match.group(1))
    return tokens_in, tokens_out


def _parse_json_line(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _first_int(source: dict, keys: list[str], fallback: int | None) -> int | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, int):
            return value
    return fallback


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
