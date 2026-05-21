from __future__ import annotations

import sqlite3

from .workers.base import WorkerResult


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


def propose_improvement(conn: sqlite3.Connection, template_id: int, minimum_samples: int = 3) -> int | None:
    rows = conn.execute(
        "SELECT * FROM job_metrics WHERE prompt_template_id = ? ORDER BY created_at DESC",
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
    body = (
        template["body"].rstrip()
        + "\n\n## Draft Improvement\n"
        + f"- Samples analyzed: {len(rows)}\n"
        + f"- Non-success outcomes: {failures}\n"
        + "- Keep acceptance criteria explicit, ownership_paths narrow, and verification commands concrete.\n"
    )
    with conn:
        cur = conn.execute(
            "INSERT INTO prompt_templates(name, version, status, body) VALUES (?, ?, 'draft', ?)",
            (template["name"], next_version, body),
        )
    return int(cur.lastrowid)
