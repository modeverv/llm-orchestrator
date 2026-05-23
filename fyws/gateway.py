from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .acceptance import parse_acceptance_defaults, project_acceptance_path, requires_forced_human_gate
from .orchestrator import compute_safe, project_list, queue_job


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


def create_project(project: str, work_root: str | Path = DEFAULT_WORK_ROOT) -> Path:
    cwd = project_dir(work_root, project)
    cwd.mkdir(parents=True, exist_ok=True)
    agents = cwd / "AGENTS.md"
    if not agents.exists():
        agents.write_text(_agents_template(project), encoding="utf-8")
    return cwd


def _agents_template(project: str) -> str:
    return f"""\
# AGENTS.md — {project}

このファイルはLLM workerがこのプロジェクトで作業するときに必ず読む指示書。

## 必須ルール

1. **commitは絶対にするな** — 変更はworking treeに置いたまま止まれ。commitはhuman_gateを通過してから人間が行う。
2. **stashは使って良い** — 作業の切り替えや退避に `git stash` を使うことは許可する。
3. **git履歴は参照して良い** — `git log`, `git diff`, `git show`, `git blame` で過去の実装を確認してよい。ただし `push`, `reset --hard`, `clean -f` などリポジトリを破壊する操作は禁止。
4. **テストが通らない状態で完了を宣言するな** — acceptance.mdの `## Verify Commands` に書かれたコマンドがすべて exit 0 になるまでは完了ではない。テストが存在しない場合は `python -m pytest -q`（またはプロジェクトの標準テストコマンド）を実行して確認すること。

## 行動規範

- 判断が必要になったらコードを書くな。`人間の判断が必要` と出力して止まれ。
- 「たぶんこれで良い」という判断を自分でするな。
- 完了できなくても、現在地を正確に記録して止まることが正解。
- ownership_paths の範囲外のファイルを変更するな。
"""


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

## Verify Commands

```bash
# Add verification commands here — they must exit 0 for the job to succeed.
# e.g.: python -m pytest -q
# e.g.: python -m py_compile path/to/file.py
```

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


def list_projects(work_root: str | Path = DEFAULT_WORK_ROOT, db_path=None) -> list[dict]:
    root = Path(work_root).expanduser().resolve()
    dirs = {p.name for p in root.iterdir() if p.is_dir()} if root.exists() else set()
    stats: dict[str, dict] = {}
    if db_path is not None:
        for row in project_list(db_path):
            stats[row["project"]] = row
    projects = []
    for name in sorted(dirs | set(stats)):
        row = {
            "project": name,
            "total": 0,
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "waiting_human": 0,
            "discarded": 0,
            "last_updated": None,
            "path": str(root / name) if name in dirs else None,
        }
        row.update(stats.get(name, {}))
        projects.append(row)
    return projects


def format_projects(projects: list[dict]) -> str:
    if not projects:
        return "no projects"
    lines = []
    for p in projects:
        parts = [p["project"], f"total={p.get('total', 0)}"]
        if p.get("running"):
            parts.append(f"running={p['running']}")
        if p.get("queued"):
            parts.append(f"queued={p['queued']}")
        if p.get("waiting_human"):
            parts.append(f"waiting={p['waiting_human']}")
        done = f"done={p.get('succeeded', 0)}✓ {p.get('failed', 0)}✗"
        if p.get("discarded"):
            done += f" {p['discarded']} discarded"
        parts.append(done)
        if p.get("last_updated"):
            parts.append(f"updated={p['last_updated']}")
        lines.append("  ".join(parts))
    return "\n".join(lines)


def split_discord_messages(text: str, limit: int = 2000) -> list[str]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if text == "":
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunk = remaining[:split_at]
        chunks.append(chunk)
        remaining = remaining[split_at:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]
    if remaining:
        chunks.append(remaining)
    return chunks


def queue_from_message(
    message: str,
    work_root: str | Path = DEFAULT_WORK_ROOT,
    mode: str | None = None,
    worker: str | None = None,
    c_score: float | None = None,
    o_score: float | None = None,
    i_score: float | None = None,
    db_path: str | Path | None = None,
    create: bool = False,
) -> QueuedGatewayJob:
    parsed = parse_project_message(message)
    cwd = project_dir(work_root, parsed.project)
    if not cwd.exists():
        if create:
            create_project(parsed.project, work_root)
        else:
            raise FileNotFoundError(cwd)
    defaults = parse_acceptance_defaults(project_acceptance_path(cwd))
    mode = mode or defaults.mode
    c_score = defaults.c_score if c_score is None else c_score
    o_score = defaults.o_score if o_score is None else o_score
    i_score = defaults.i_score if i_score is None else i_score
    ownership_paths = defaults.ownership_paths
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
    status = "waiting_human" if safe < 0.3 or requires_forced_human_gate(mode, parsed.instruction) else "queued"
    return QueuedGatewayJob(job_id, parsed.project, worker or parsed.worker, safe, status, cwd, task, acceptance)


def discover_ownership_paths(project_path: str | Path) -> list[str]:
    return parse_acceptance_defaults(project_acceptance_path(project_path)).ownership_paths
