from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import acceptance, evaluator, gate, lock, summarizer, verifier
from .db import DEFAULT_DB_PATH, connect, init_db
from .workers.base import WorkerBase, WorkerResult
from .workers.claude import ClaudeWorker
from .workers.codex import CodexWorker
from .workers.gemini import GeminiWorker


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts"


def compute_safe(c_score: float, o_score: float, i_score: float) -> float:
    for value in (c_score, o_score, i_score):
        if value < 0 or value > 1:
            raise ValueError("safe scores must be between 0 and 1")
    return c_score * o_score * (1 - i_score)


def queue_job(
    project: str,
    prompt_path: str | Path,
    cwd: str | Path,
    mode: str | None = None,
    worker: str = "gemini",
    c_score: float | None = None,
    o_score: float | None = None,
    i_score: float | None = None,
    ownership_paths: list[str] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    init_db(db_path)
    prompt = Path(prompt_path).resolve()
    if not prompt.exists():
        raise FileNotFoundError(prompt)
    resolved_cwd = Path(cwd).resolve()
    defaults = acceptance.parse_acceptance_defaults(acceptance.project_acceptance_path(resolved_cwd))
    mode = mode or defaults.mode
    c_score = defaults.c_score if c_score is None else c_score
    o_score = defaults.o_score if o_score is None else o_score
    i_score = defaults.i_score if i_score is None else i_score
    ownership_paths = defaults.ownership_paths if ownership_paths is None else ownership_paths
    task_text = prompt.read_text(encoding="utf-8", errors="replace")
    safe_score = compute_safe(c_score, o_score, i_score)
    forced_gate_reason = acceptance.requires_forced_human_gate(mode, task_text)
    with connect(db_path) as conn:
        prompt_template_id = evaluator.select_active_template(conn, project)
        cur = conn.execute(
            """
            INSERT INTO jobs(
                project, prompt_path, cwd, mode, worker, status, safe_score,
                c_score, o_score, i_score, ownership_paths, prompt_template_id
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (
                project,
                str(prompt),
                str(resolved_cwd),
                mode,
                worker,
                safe_score,
                c_score,
                o_score,
                i_score,
                json.dumps(ownership_paths),
                prompt_template_id,
            ),
        )
        job_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO job_events(job_id, event_type, message, payload) VALUES (?, 'queued', ?, '{}')",
            (job_id, f"queued {project} safe={safe_score:.3f}"),
        )
        if forced_gate_reason:
            gate.open_gate(
                conn,
                job_id,
                "This job may involve deploy, DB, or secret operations. Human approval is required before execution.",
                forced_gate_reason,
            )
        elif safe_score < 0.3:
            gate.open_gate(
                conn,
                job_id,
                f"safe(T)={safe_score:.3f} is below 0.3. Approve running this job?",
                "safe_below_threshold",
            )
    return job_id


def dispatch_next(db_path: str | Path = DEFAULT_DB_PATH) -> int | None:
    init_db(db_path)
    with connect(db_path) as conn:
        job = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
        ).fetchone()
    if job is None:
        return None
    run_job(job["id"], db_path=db_path)
    return int(job["id"])


def run_job(
    job_id: int,
    db_path: str | Path = DEFAULT_DB_PATH,
    worker_impl: WorkerBase | None = None,
    worker_timeout_seconds: float | None = None,
) -> WorkerResult:
    init_db(db_path)
    with connect(db_path) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"job {job_id} not found")
        if job["status"] == "waiting_human":
            raise ValueError(f"job {job_id} is waiting for human input")
        if not lock.acquire_lock(conn, job_id, job["project"], job["cwd"], job["mode"]):
            _event(conn, job_id, "lock_conflict", "lock conflict; job remains queued")
            return WorkerResult(False, "", "", error="lock conflict")

    start = time.monotonic()
    artifact_dir = ARTIFACTS_DIR / str(job_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ownership_paths = json.loads(job["ownership_paths"])
    result = WorkerResult(False, "", str(artifact_dir / "events.jsonl"), error="not run")
    try:
        _clear_artifacts(artifact_dir)
        _prepare_artifacts(job, artifact_dir, db_path)
        ownership_baseline = git_status_paths(job["cwd"])
        worker = worker_impl or route_worker(job["worker"])
        with connect(db_path) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running', attempts = attempts + 1,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
            _event(conn, job_id, "running", "job started")

        result = worker.run(
            job["prompt_path"],
            job["cwd"],
            artifact_dir,
            ownership_paths,
            resume=_should_resume(job),
            timeout_seconds=worker_timeout_seconds,
        )
        if worker_requires_human(result.last_message):
            with connect(db_path) as conn:
                gate.open_gate(
                    conn,
                    job_id,
                    "Worker explicitly requested human judgment. Review before continuing.",
                    "worker_requested_human",
                )
                _event(conn, job_id, "error", "worker requested human judgment")
            return result
        out_of_scope = ownership_check(job["cwd"], ownership_paths, baseline_paths=ownership_baseline)
        if out_of_scope:
            result.out_of_scope_files.extend(out_of_scope)
            _write_diff(job["cwd"], artifact_dir)
            with connect(db_path) as conn:
                gate.open_gate(
                    conn,
                    job_id,
                    "Worker changed files outside ownership_paths. Review before continuing.",
                    "out_of_scope_changes",
                )
                _event(conn, job_id, "error", "out-of-scope changes detected", {"files": out_of_scope})
            return result

        if result.success:
            acceptance_path = _find_acceptance_path(job)
            if acceptance_path:
                verify_ok, verify_outputs = verifier.run_verify(acceptance_path, job["cwd"])
                if not verify_ok:
                    with connect(db_path) as conn:
                        gate.open_gate(
                            conn,
                            job_id,
                            "Verification commands failed.\n" + "\n---\n".join(verify_outputs[-2:]),
                            "verify_failed",
                        )
                        _event(conn, job_id, "error", "verification failed", {"outputs": verify_outputs})
                    return WorkerResult(
                        False,
                        result.last_message,
                        result.events_path,
                        tokens_in=result.tokens_in,
                        tokens_out=result.tokens_out,
                        step_count=result.step_count,
                        error="verification failed",
                    )

        status = "succeeded" if result.success else "failed"
        if not result.success and _should_gate_after_failure(db_path, job_id):
            with connect(db_path) as conn:
                gate.open_gate(
                    conn,
                    job_id,
                    "This job failed twice consecutively. Give guidance before retrying.",
                    "two_consecutive_failures",
                )
                _event(conn, job_id, "error", result.error or "worker failed")
            return result

        duration = time.monotonic() - start
        summarizer.summarize(artifact_dir, Path(job["prompt_path"]).read_text(encoding="utf-8"), job["cwd"], status, result.last_message)
        with connect(db_path) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, last_error = ?, finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, result.error, job_id),
            )
            _event(conn, job_id, status, result.error or "job completed")
            evaluator.record_metrics(conn, job_id, job["worker"], status, duration, result, job["prompt_template_id"])
        return result
    except Exception as exc:
        with connect(db_path) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', last_error = ?, finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(exc), job_id),
            )
            _event(conn, job_id, "error", str(exc))
        return WorkerResult(False, result.last_message, result.events_path, error=str(exc))
    finally:
        with connect(db_path) as conn:
            lock.release_lock(conn, job_id)


def route_worker(name: str) -> WorkerBase:
    if name == "gemini":
        return GeminiWorker()
    if name == "claude":
        return ClaudeWorker()
    if name == "codex":
        return CodexWorker()
    raise ValueError(f"unknown worker: {name}")


def ownership_check(cwd: str | Path, ownership_paths: list[str], baseline_paths: set[str] | None = None) -> list[str]:
    if not ownership_paths:
        return []
    changed = sorted(git_status_paths(cwd) - (baseline_paths or set()))
    allowed = [path.rstrip("/") for path in ownership_paths]
    return [path for path in changed if not any(_is_allowed(path, prefix) for prefix in allowed)]


def git_status_paths(cwd: str | Path) -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return set()
    return set(_changed_paths_from_porcelain(proc.stdout))


def _changed_paths_from_porcelain(output: str) -> list[str]:
    paths: list[str] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        path = raw[3:] if len(raw) > 3 else raw.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path:
            paths.append(path)
    return paths


def list_jobs(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [dict(row) for row in rows]


def retry_job(job_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    with connect(db_path) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"job {job_id} not found")
        cur = conn.execute(
            """
            INSERT INTO jobs(
                project, prompt_path, cwd, mode, worker, status, safe_score,
                c_score, o_score, i_score, ownership_paths, prompt_template_id
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (
                job["project"],
                job["prompt_path"],
                job["cwd"],
                job["mode"],
                job["worker"],
                job["safe_score"],
                job["c_score"],
                job["o_score"],
                job["i_score"],
                job["ownership_paths"],
                job["prompt_template_id"],
            ),
        )
        return int(cur.lastrowid)


def log_lines(job_id: int) -> list[str]:
    path = ARTIFACTS_DIR / str(job_id) / "events.jsonl"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def dry_run_check(
    project: str,
    cwd: str | Path,
    mode: str | None,
    c_score: float | None,
    o_score: float | None,
    i_score: float | None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, object]:
    init_db(db_path)
    resolved_cwd = Path(cwd).resolve()
    defaults = acceptance.parse_acceptance_defaults(acceptance.project_acceptance_path(resolved_cwd))
    mode = mode or defaults.mode
    c_score = defaults.c_score if c_score is None else c_score
    o_score = defaults.o_score if o_score is None else o_score
    i_score = defaults.i_score if i_score is None else i_score
    safe_score = compute_safe(c_score, o_score, i_score)
    with connect(db_path) as conn:
        conflict = lock.check_conflict(conn, project, str(resolved_cwd), mode)
    forced_gate_reason = acceptance.requires_forced_human_gate(mode, "")
    return {
        "safe_score": safe_score,
        "requires_human_gate": safe_score < 0.3 or forced_gate_reason is not None,
        "human_gate_reason": forced_gate_reason or ("safe_below_threshold" if safe_score < 0.3 else None),
        "lock_conflict": conflict,
        "mode": mode,
        "c_score": c_score,
        "o_score": o_score,
        "i_score": i_score,
    }


def _prepare_artifacts(job, artifact_dir: Path, db_path: str | Path) -> None:
    shutil.copyfile(job["prompt_path"], artifact_dir / "prompt.md")
    cwd = Path(job["cwd"])
    agents = cwd / "AGENTS.md"
    task_acceptance = Path(job["prompt_path"]).parent / "task.acceptance.md"
    project_acceptance = cwd / "ACCEPTANCE.md"
    acceptance = task_acceptance if task_acceptance.exists() else project_acceptance
    site_context = cwd / "SITE_CONTEXT.md"
    summarizer.build_context(
        artifact_dir,
        agents,
        job["prompt_path"],
        previous_summary_path=_previous_summary_path(job, db_path),
        acceptance_path=acceptance if acceptance.exists() else None,
        diff_path=(artifact_dir / "diff.patch") if (artifact_dir / "diff.patch").exists() else None,
        site_context_path=site_context if site_context.exists() else None,
    )


def _clear_artifacts(artifact_dir: Path) -> None:
    for name in ("prompt.md", "events.jsonl", "last_message.txt", "summary.md", "context.md", "diff.patch"):
        path = artifact_dir / name
        if path.exists():
            path.unlink()


def _event(conn, job_id: int, event_type: str, message: str, payload: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO job_events(job_id, event_type, message, payload) VALUES (?, ?, ?, ?)",
        (job_id, event_type, message, json.dumps(payload or {})),
    )


def _should_gate_after_failure(db_path: str | Path, job_id: int) -> bool:
    with connect(db_path) as conn:
        job = conn.execute("SELECT attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(job and job["attempts"] >= 2)


def _write_diff(cwd: str | Path, artifact_dir: Path) -> None:
    proc = subprocess.run(["git", "diff"], cwd=str(cwd), text=True, capture_output=True, check=False)
    (artifact_dir / "diff.patch").write_text(proc.stdout, encoding="utf-8")


def _is_allowed(path: str, prefix: str) -> bool:
    if prefix in ("", "."):
        return True
    return path == prefix or path.startswith(prefix + "/")


def _previous_summary_path(job, db_path: str | Path) -> Path | None:
    with connect(db_path) as conn:
        previous = conn.execute(
            """
            SELECT id FROM jobs
            WHERE project = ? AND id < ? AND status IN ('succeeded', 'failed', 'waiting_human')
            ORDER BY id DESC
            LIMIT 1
            """,
            (job["project"], job["id"]),
        ).fetchone()
    if previous is None:
        return None
    path = ARTIFACTS_DIR / str(previous["id"]) / "summary.md"
    return path if path.exists() else None


def recover_stuck_jobs(db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    """Reset jobs stuck in 'running' at crash time back to 'queued'."""
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM jobs WHERE status = 'running'").fetchall()
        ids = [int(row["id"]) for row in rows]
        for job_id in ids:
            conn.execute(
                "UPDATE jobs SET status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job_id,),
            )
            _event(conn, job_id, "queued", "recovered from stuck running state after restart")
            lock.release_lock(conn, job_id)
    return ids


def _should_resume(job) -> bool:
    return job["worker"] == "gemini" and job["attempts"] > 0 and bool(job["gemini_session_id"])


def project_list(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT project,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                   SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                   SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN status = 'waiting_human' THEN 1 ELSE 0 END) AS waiting_human,
                   MAX(updated_at) AS last_updated
            FROM jobs
            GROUP BY project
            ORDER BY project
            """
        ).fetchall()
        return [dict(row) for row in rows]


def _find_acceptance_path(job) -> Path | None:
    task_acceptance = Path(job["prompt_path"]).parent / "task.acceptance.md"
    if task_acceptance.exists():
        return task_acceptance
    project_acceptance = Path(job["cwd"]) / "ACCEPTANCE.md"
    if project_acceptance.exists():
        return project_acceptance
    return None


def worker_requires_human(message: str) -> bool:
    lower = message.lower()
    patterns = [
        r"判断が必要",
        r"人間(?:の)?判断",
        r"人間(?:の)?確認",
        r"人間(?:の)?承認",
        r"確認してください",
        r"承認してください",
        r"勝手に(?:進め|判断)られ",
        r"human[-\s_]*(?:gate|approval|review|judg(?:e)?ment|decision|input)",
        r"(?:requires?|needs?|need|requesting)\s+(?:a\s+)?human",
        r"(?:cannot|can't|shouldn't|must not)\s+(?:proceed|continue|decide)\s+without\s+(?:human|approval)",
        r"(?:please|needs?|requires?)\s+(?:approve|confirm|review)",
        r"manual\s+(?:approval|review|intervention|decision)",
        r"blocked\s+(?:on|by)\s+(?:human|approval|decision)",
    ]
    return any(re.search(pattern, lower) or re.search(pattern, message) for pattern in patterns)
