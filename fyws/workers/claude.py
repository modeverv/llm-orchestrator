from __future__ import annotations

import subprocess
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
    ) -> WorkerResult:
        prompt = Path(prompt_path).read_text(encoding="utf-8")
        artifact = Path(artifact_dir)
        artifact.mkdir(parents=True, exist_ok=True)
        events_path = artifact / "events.jsonl"

        try:
            proc = subprocess.run(
                [self.executable, "--print", "--permission-mode", "acceptEdits"],
                input=prompt,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            error = f"claude executable not found: {exc}"
            events_path.write_text(error + "\n", encoding="utf-8")
            return WorkerResult(False, "", str(events_path), error=error)

        output = proc.stdout
        if proc.stderr:
            output = output + ("\n" if output else "") + proc.stderr
        events_path.write_text(output, encoding="utf-8")
        last_message = output.strip().splitlines()[-1] if output.strip() else ""
        (artifact / "last_message.txt").write_text(last_message, encoding="utf-8")
        success = proc.returncode == 0
        return WorkerResult(
            success=success,
            last_message=last_message,
            events_path=str(events_path),
            step_count=len(output.splitlines()),
            error=None if success else f"claude exited with code {proc.returncode}",
        )
