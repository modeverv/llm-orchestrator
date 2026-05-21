from __future__ import annotations

import sqlite3


def open_gate(conn: sqlite3.Connection, job_id: int, question: str, reason: str) -> None:
    with conn:
        conn.execute(
            "UPDATE jobs SET status = 'waiting_human', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job_id,),
        )
        conn.execute(
            "INSERT INTO human_requests(job_id, question, reason) VALUES (?, ?, ?)",
            (job_id, question, reason),
        )
        conn.execute(
            "INSERT INTO job_events(job_id, event_type, message, payload) VALUES (?, 'human_gate', ?, '{}')",
            (job_id, question),
        )


def list_open_gates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT human_requests.*, jobs.project, jobs.status
        FROM human_requests
        JOIN jobs ON jobs.id = human_requests.job_id
        WHERE human_requests.status = 'open'
        ORDER BY human_requests.created_at
        """
    ).fetchall()


def answer_gate(conn: sqlite3.Connection, job_id: int, answer: str) -> None:
    with conn:
        request = conn.execute(
            """
            SELECT id FROM human_requests
            WHERE job_id = ? AND status = 'open'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if request is None:
            raise ValueError(f"no open human gate for job {job_id}")
        conn.execute(
            """
            UPDATE human_requests
            SET answer = ?, status = 'answered', answered_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (answer, request["id"]),
        )
        conn.execute(
            "UPDATE jobs SET status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job_id,),
        )
        conn.execute(
            "INSERT INTO job_events(job_id, event_type, message, payload) VALUES (?, 'gate_answer', ?, '{}')",
            (job_id, answer),
        )
