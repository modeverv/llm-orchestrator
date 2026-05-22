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


class GeminiWorker:
    def __init__(self, executable: str = "gemini", model: str = "gemini-2.5-pro") -> None:
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
        last_message = ""
        step_count = 0
        tokens_in: int | None = None
        tokens_out: int | None = None

        cmd = [
            self.executable,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--approval-mode",
            "yolo",
        ]
        if resume:
            cmd.insert(1, "--resume")
            cmd.insert(2, "latest")
        else:
            cmd.extend(["--model", self.model])

        try:
            with events_path.open("w", encoding="utf-8") as events:
                start = time.monotonic()
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
                assert proc.stdout is not None
                selector = selectors.DefaultSelector()
                selector.register(proc.stdout, selectors.EVENT_READ)
                while proc.poll() is None:
                    if _timed_out(start, timeout_seconds):
                        _terminate_process(proc)
                        error = f"gemini timed out after {timeout_seconds:g}s"
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
        except FileNotFoundError as exc:
            error = f"gemini executable not found: {exc}"
            events_path.write_text(json.dumps({"event_type": "error", "message": error}) + "\n", encoding="utf-8")
            return WorkerResult(False, "", str(events_path), error=error)

        success = returncode == 0
        error = None if success else f"gemini exited with code {returncode}"
        last_path = artifact / "last_message.txt"
        last_path.write_text(last_message, encoding="utf-8")
        return WorkerResult(
            success=success,
            last_message=last_message,
            events_path=str(events_path),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            step_count=step_count,
            error=error,
        )


def _record_line(events, line: str, last_message: str, tokens_in: int | None, tokens_out: int | None, step_count: int):
    events.write(line)
    events.flush()
    step_count += 1
    parsed = _parse_json_line(line)
    if parsed is None:
        if line.strip():
            last_message = line.strip()
        return last_message, tokens_in, tokens_out, step_count
    command = _extract_command(parsed)
    if command:
        events.write(json.dumps({"event_type": "command", "command": command}, ensure_ascii=False) + "\n")
        events.flush()
    message = _extract_message(parsed)
    if message:
        last_message = message
    usage = parsed.get("usage") or parsed.get("usageMetadata") or {}
    tokens_in = _first_int(usage, ["promptTokenCount", "input_tokens", "tokens_in"], tokens_in)
    tokens_out = _first_int(usage, ["candidatesTokenCount", "output_tokens", "tokens_out"], tokens_out)
    return last_message, tokens_in, tokens_out, step_count


def _timed_out(start: float, timeout_seconds: float | None) -> bool:
    return timeout_seconds is not None and time.monotonic() - start >= timeout_seconds


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
    for key in ("text", "message", "content", "last_message"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    candidates = event.get("candidates")
    if isinstance(candidates, list) and candidates:
        content = candidates[0].get("content", {}) if isinstance(candidates[0], dict) else {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        return "\n".join(text for text in texts if text).strip()
    return ""


def _extract_command(event: dict) -> str:
    direct = _command_from_mapping(event)
    if direct:
        return direct
    for call in _iter_tool_calls(event):
        command = _command_from_mapping(call)
        if command:
            return command
        args = call.get("args") or call.get("arguments") or call.get("input") or {}
        if isinstance(args, str):
            parsed_args = _parse_json_line(args)
            args = parsed_args if parsed_args is not None else {"command": args}
        if isinstance(args, dict):
            command = _command_from_mapping(args)
            if command:
                return command
    message = _extract_message(event)
    return _command_from_text(message)


def _iter_tool_calls(value):
    if isinstance(value, dict):
        if any(key in value for key in ("functionCall", "function_call", "tool_call")):
            for key in ("functionCall", "function_call", "tool_call"):
                call = value.get(key)
                if isinstance(call, dict):
                    yield call
        calls = value.get("tool_calls") or value.get("function_calls")
        if isinstance(calls, list):
            for call in calls:
                if isinstance(call, dict):
                    yield call
        for nested in value.values():
            yield from _iter_tool_calls(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_tool_calls(item)


def _command_from_mapping(value: dict) -> str:
    for key in ("command", "cmd", "shell_command"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    argv = value.get("argv")
    if isinstance(argv, list) and argv:
        return " ".join(str(part) for part in argv)
    return ""


def _command_from_text(text: str) -> str:
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("$ "):
            return stripped[2:].strip()
        match = re.match(r"^(?:run|exec|shell|bash|command):\s*(.+)$", stripped, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _first_int(source: dict, keys: list[str], fallback: int | None) -> int | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, int):
            return value
    return fallback
