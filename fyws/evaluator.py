from __future__ import annotations

import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

from .workers.base import WorkerResult

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts"
ImprovementProposer = Callable[[str], str]


def record_metrics(
    conn: sqlite3.Connection,
    job_id: int,
    worker: str,
    outcome: str,
    duration_seconds: float,
    result: WorkerResult,
    prompt_template_id: int | None = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO job_metrics(
                job_id, prompt_template_id, worker, outcome, duration_seconds,
                tokens_in, tokens_out, step_count, out_of_scope_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                prompt_template_id,
                worker,
                outcome,
                duration_seconds,
                result.tokens_in,
                result.tokens_out,
                result.step_count,
                len(result.out_of_scope_files),
            ),
        )


def metrics_summary(conn: sqlite3.Connection) -> dict[str, object]:
    total = conn.execute("SELECT COUNT(*) AS count FROM job_metrics").fetchone()["count"]
    by_outcome = {
        row["outcome"]: row["count"]
        for row in conn.execute("SELECT outcome, COUNT(*) AS count FROM job_metrics GROUP BY outcome")
    }
    averages = conn.execute(
        """
        SELECT AVG(duration_seconds) AS duration, AVG(tokens_in) AS tokens_in, AVG(tokens_out) AS tokens_out
        FROM job_metrics
        """
    ).fetchone()
    return {
        "total": total,
        "by_outcome": by_outcome,
        "avg_duration_seconds": averages["duration"] or 0,
        "avg_tokens_in": averages["tokens_in"] or 0,
        "avg_tokens_out": averages["tokens_out"] or 0,
    }


def approve_template(conn: sqlite3.Connection, template_id: int) -> None:
    with conn:
        template = conn.execute("SELECT * FROM prompt_templates WHERE id = ?", (template_id,)).fetchone()
        if template is None:
            raise ValueError(f"template {template_id} not found")
        conn.execute(
            "UPDATE prompt_templates SET status = 'deprecated' WHERE name = ? AND status = 'active'",
            (template["name"],),
        )
        conn.execute(
            "UPDATE prompt_templates SET status = 'active', approved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (template_id,),
        )


def select_active_template(conn: sqlite3.Connection, project: str, fallback_name: str = "default") -> int | None:
    for name in (project, fallback_name):
        row = conn.execute(
            """
            SELECT id FROM prompt_templates
            WHERE name = ? AND status = 'active'
            ORDER BY version DESC, id DESC
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
    return None


def propose_improvement(
    conn: sqlite3.Connection,
    template_id: int,
    minimum_samples: int = 3,
    proposer: ImprovementProposer | None = None,
    artifacts_dir: str | Path = ARTIFACTS_DIR,
) -> int | None:
    rows = conn.execute(
        """
        SELECT jm.*, j.project, j.prompt_path, j.cwd, j.last_error
        FROM job_metrics jm
        JOIN jobs j ON j.id = jm.job_id
        WHERE jm.prompt_template_id = ?
        ORDER BY jm.created_at DESC, jm.id DESC
        """,
        (template_id,),
    ).fetchall()
    if len(rows) < minimum_samples:
        return None
    template = conn.execute("SELECT * FROM prompt_templates WHERE id = ?", (template_id,)).fetchone()
    if template is None:
        raise ValueError(f"template {template_id} not found")
    failures = sum(1 for row in rows if row["outcome"] != "succeeded")
    next_version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM prompt_templates WHERE name = ?",
        (template["name"],),
    ).fetchone()["version"]
    prompt = _improvement_prompt(template, rows, failures, Path(artifacts_dir))
    body = (proposer or _call_default_llm)(prompt).strip()
    if not body:
        raise ValueError("LLM proposer returned an empty prompt template")
    with conn:
        cur = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES (?, ?, 'draft', ?)",
            (template["name"], next_version, body),
        )
    return int(cur.lastrowid)


def _improvement_prompt(template: sqlite3.Row, rows: list[sqlite3.Row], failures: int, artifacts_dir: Path) -> str:
    lines = [
        "You improve FYWS prompt templates.",
        "Return only the full replacement prompt template body. Do not include commentary.",
        "The template must keep acceptance criteria explicit, ownership_paths narrow, verification commands concrete, and must not mark itself active.",
        "",
        f"Template name: {template['name']}",
        f"Current version: {template['version']}",
        f"Samples analyzed: {len(rows)}",
        f"Non-success outcomes: {failures}",
        "",
        "## Current Template",
        template["body"].rstrip(),
        "",
        "## Metrics",
    ]
    for row in rows[:20]:
        lines.append(
            "- "
            f"job_id={row['job_id']} "
            f"project={row['project']} "
            f"worker={row['worker']} "
            f"outcome={row['outcome']} "
            f"duration_seconds={row['duration_seconds']} "
            f"tokens_in={row['tokens_in']} "
            f"tokens_out={row['tokens_out']} "
            f"steps={row['step_count']} "
            f"out_of_scope={row['out_of_scope_count']}"
        )
    failure_lines = _failure_summary_lines(rows, artifacts_dir)
    if failure_lines:
        lines.extend(["", "## Failure Summaries", *failure_lines])
    return "\n".join(lines).rstrip() + "\n"


def _failure_summary_lines(rows: list[sqlite3.Row], artifacts_dir: Path) -> list[str]:
    lines: list[str] = []
    for row in rows:
        if row["outcome"] == "succeeded":
            continue
        lines.append(f"### job_id={row['job_id']} outcome={row['outcome']}")
        if row["last_error"]:
            lines.append(f"last_error: {row['last_error']}")
        summary = artifacts_dir / str(row["job_id"]) / "summary.md"
        if summary.exists():
            lines.append(_clip(summary.read_text(encoding="utf-8", errors="replace"), 4000))
    return lines


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n...[truncated]"


def _call_default_llm(prompt: str) -> str:
    gemini = shutil.which("gemini")
    if gemini is None:
        raise RuntimeError("gemini CLI not found; pass a proposer callable or install Gemini CLI")
    proc = subprocess.run(
        [gemini, "-p", prompt],
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gemini exited with {proc.returncode}")
    return proc.stdout
