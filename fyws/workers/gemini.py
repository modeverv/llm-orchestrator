from __future__ import annotations

import json
import subprocess
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
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    events.write(line)
                    events.flush()
                    step_count += 1
                    parsed = _parse_json_line(line)
                    if parsed is None:
                        if line.strip():
                            last_message = line.strip()
                        continue
                    message = _extract_message(parsed)
                    if message:
                        last_message = message
                    usage = parsed.get("usage") or parsed.get("usageMetadata") or {}
                    tokens_in = _first_int(usage, ["promptTokenCount", "input_tokens", "tokens_in"], tokens_in)
                    tokens_out = _first_int(usage, ["candidatesTokenCount", "output_tokens", "tokens_out"], tokens_out)
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


def _first_int(source: dict, keys: list[str], fallback: int | None) -> int | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, int):
            return value
    return fallback
