from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class WorkerResult:
    success: bool
    last_message: str
    events_path: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    step_count: int = 0
    out_of_scope_files: list[str] = field(default_factory=list)
    error: str | None = None


class WorkerBase(Protocol):
    def run(
        self,
        prompt_path: str | Path,
        cwd: str | Path,
        artifact_dir: str | Path,
        ownership_paths: list[str],
        resume: bool = False,
        timeout_seconds: float | None = None,
    ) -> WorkerResult:
        ...
