from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .orchestrator import compute_safe, queue_job


@dataclass(frozen=True)
class GatewayJob:
    project: str
    instruction: str


def parse_project_message(message: str) -> GatewayJob:
    text = message.strip().strip("「」")
    if ":" not in text:
        raise ValueError("message must be '<project>: <instruction>'")
    project, instruction = text.split(":", 1)
    project = project.strip()
    instruction = instruction.strip()
    if not project or not instruction:
        raise ValueError("project and instruction are required")
    return GatewayJob(project, instruction)


def project_dir(work_root: str | Path, project: str) -> Path:
    return Path(work_root).expanduser().resolve() / project


def write_task_file(project_path: str | Path, instruction: str) -> Path:
    task_path = Path(project_path) / "task.md"
    task_path.write_text(instruction + "\n", encoding="utf-8")
    return task_path


def queue_from_message(
    message: str,
    work_root: str | Path = "~/work",
    mode: str = "write",
    worker: str = "gemini",
    c_score: float = 0.8,
    o_score: float = 0.8,
    i_score: float = 0.2,
    db_path: str | Path | None = None,
) -> tuple[int, float]:
    parsed = parse_project_message(message)
    cwd = project_dir(work_root, parsed.project)
    if not cwd.exists():
        raise FileNotFoundError(cwd)
    task = write_task_file(cwd, parsed.instruction)
    safe = compute_safe(c_score, o_score, i_score)
    kwargs = {}
    if db_path is not None:
        kwargs["db_path"] = db_path
    job_id = queue_job(
        parsed.project,
        task,
        cwd,
        mode=mode,
        worker=worker,
        c_score=c_score,
        o_score=o_score,
        i_score=i_score,
        ownership_paths=[],
        **kwargs,
    )
    return job_id, safe
