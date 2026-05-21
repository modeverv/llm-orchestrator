from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .orchestrator import compute_safe, queue_job


DEFAULT_WORK_ROOT = "~/work/001_work/by-llms"
SUPPORTED_WORKERS = {"gemini", "claude", "codex"}


@dataclass(frozen=True)
class GatewayJob:
    project: str
    instruction: str
    worker: str = "gemini"


@dataclass(frozen=True)
class QueuedGatewayJob:
    job_id: int
    project: str
    worker: str
    safe_score: float
    status: str
    cwd: Path
    task_path: Path
    acceptance_path: Path


def parse_project_message(message: str) -> GatewayJob:
    text = message.strip().strip("「」")
    if ":" not in text:
        raise ValueError("message must be '<project>: <instruction>' or '<worker> <project>: <instruction>'")
    project, instruction = text.split(":", 1)
    project = project.strip()
    instruction = instruction.strip()
    worker = "gemini"
    parts = project.split(maxsplit=1)
    if len(parts) == 2 and parts[0] in SUPPORTED_WORKERS:
        worker, project = parts[0], parts[1].strip()
    if not project or not instruction:
        raise ValueError("project and instruction are required")
    return GatewayJob(project, instruction, worker)


def project_dir(work_root: str | Path, project: str) -> Path:
    return Path(work_root).expanduser().resolve() / project


def write_task_file(project_path: str | Path, instruction: str) -> Path:
    task_path = Path(project_path) / "task.md"
    task_path.write_text(instruction + "\n", encoding="utf-8")
    return task_path


def write_acceptance_file(
    project_path: str | Path,
    instruction: str,
    mode: str,
    ownership_paths: list[str],
    c_score: float,
    o_score: float,
    i_score: float,
) -> Path:
    safe = compute_safe(c_score, o_score, i_score)
    path = Path(project_path) / "task.acceptance.md"
    ownership = "\n".join(f"    - {item}" for item in ownership_paths) or "    - ."
    content = f"""# ACCEPTANCE.md

## safe(T) Score

- C: {c_score}
- O: {o_score}
- I: {i_score}
- safe = C x O x (1 - I): {safe:.3f}

## User Instruction

{instruction}

## Completion Criteria

- [ ] Worker changed only files inside ownership paths.
- [ ] Relevant verification commands were run or explicitly documented as not run.
- [ ] Final state is recorded in summary.md.

## Ownership

```yaml
ownership:
  mode: {mode}
  paths:
{ownership}
```

## Human Gate

- safe < 0.3 requires human_gate before execution.
- Out-of-scope changes require human review.
- Worker uncertainty requires human review.
"""
    path.write_text(content, encoding="utf-8")
    return path


def format_queued(job: QueuedGatewayJob) -> str:
    prefix = "waiting_human" if job.status == "waiting_human" else "queued"
    return f"{job.project} #{job.job_id} {prefix} worker={job.worker} (safe={job.safe_score:.3f})"


def format_gate(job_id: int, project: str, reason: str, question: str) -> str:
    return f"{project} #{job_id} human_gate [{reason}]\n{question}"


def format_completion(job_id: int, project: str, status: str) -> str:
    return f"{project} #{job_id} {status}"


def queue_from_message(
    message: str,
    work_root: str | Path = DEFAULT_WORK_ROOT,
    mode: str = "write",
    worker: str | None = None,
    c_score: float = 0.8,
    o_score: float = 0.8,
    i_score: float = 0.2,
    db_path: str | Path | None = None,
) -> QueuedGatewayJob:
    parsed = parse_project_message(message)
    cwd = project_dir(work_root, parsed.project)
    if not cwd.exists():
        raise FileNotFoundError(cwd)
    ownership_paths = discover_ownership_paths(cwd)
    task = write_task_file(cwd, parsed.instruction)
    acceptance = write_acceptance_file(
        cwd,
        parsed.instruction,
        mode,
        ownership_paths,
        c_score,
        o_score,
        i_score,
    )
    safe = compute_safe(c_score, o_score, i_score)
    kwargs = {}
    if db_path is not None:
        kwargs["db_path"] = db_path
    job_id = queue_job(
        parsed.project,
        task,
        cwd,
        mode=mode,
        worker=worker or parsed.worker,
        c_score=c_score,
        o_score=o_score,
        i_score=i_score,
        ownership_paths=ownership_paths,
        **kwargs,
    )
    status = "waiting_human" if safe < 0.3 else "queued"
    return QueuedGatewayJob(job_id, parsed.project, worker or parsed.worker, safe, status, cwd, task, acceptance)


def discover_ownership_paths(project_path: str | Path) -> list[str]:
    acceptance = Path(project_path) / "ACCEPTANCE.md"
    if not acceptance.exists():
        return ["."]
    paths: list[str] = []
    in_paths = False
    for raw in acceptance.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "paths:":
            in_paths = True
            continue
        if in_paths:
            if line.startswith("- "):
                paths.append(line[2:].strip())
                continue
            if line and not raw.startswith(" "):
                break
    return paths or ["."]
