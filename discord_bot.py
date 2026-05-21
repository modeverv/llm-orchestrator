from __future__ import annotations

import argparse
import os

from fyws import gate, gateway, orchestrator
from fyws.db import DEFAULT_DB_PATH, connect


def main() -> int:
    parser = argparse.ArgumentParser(description="FYWS Discord gateway helper")
    parser.add_argument(
        "message",
        nargs="?",
        help="'<project>: <instruction>', 'status', 'log <job-id>', 'gate', or 'answer <job-id> <text>'",
    )
    parser.add_argument("--work-root", default=os.environ.get("FYWS_WORK_ROOT", "~/work"))
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    if not args.message:
        parser.print_help()
        return 0

    message = args.message.strip()
    if message == "status":
        for job in orchestrator.list_jobs(args.db):
            print(f"#{job['id']} {job['project']} {job['status']} safe={job['safe_score']:.3f}")
        return 0
    if message == "gate":
        with connect(args.db) as conn:
            for row in gate.list_open_gates(conn):
                print(f"#{row['job_id']} {row['project']} {row['reason']}: {row['question']}")
        return 0
    if message.startswith("answer "):
        _, job_id, answer = message.split(maxsplit=2)
        with connect(args.db) as conn:
            gate.answer_gate(conn, int(job_id.lstrip("#")), answer)
        print(f"queued #{job_id.lstrip('#')}")
        return 0
    if message.startswith("log "):
        job_id = int(message.split(maxsplit=1)[1].lstrip("#"))
        summary = orchestrator.ARTIFACTS_DIR / str(job_id) / "summary.md"
        if summary.exists():
            print(summary.read_text(encoding="utf-8"))
        return 0

    job_id, safe = gateway.queue_from_message(message, args.work_root, db_path=args.db)
    print(f"queued #{job_id} safe={safe:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
