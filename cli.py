from __future__ import annotations

import argparse
import json
from pathlib import Path

from fyws import evaluator, gate, orchestrator, runner
from fyws.db import DEFAULT_DB_PATH, connect, init_db
from fyws.gateway import DEFAULT_WORK_ROOT


WORKER_CHOICES = ["gemini", "claude", "codex"]


def main() -> int:
    parser = argparse.ArgumentParser(prog="fyws")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    job = sub.add_parser("job")
    job_sub = job.add_subparsers(dest="job_command", required=True)
    add = job_sub.add_parser("add")
    add.add_argument("--project", required=True)
    add.add_argument("--prompt", required=True)
    add.add_argument("--cwd", default=".")
    add.add_argument("--mode", choices=["read", "write", "deploy"], default="write")
    add.add_argument("--worker", choices=WORKER_CHOICES, default="gemini")
    add.add_argument("--c", type=float, default=0.8)
    add.add_argument("--o", type=float, default=0.8)
    add.add_argument("--i", type=float, default=0.2)
    add.add_argument("--ownership", action="append", default=[])
    add.add_argument("--dry-run", action="store_true")

    run = job_sub.add_parser("run")
    run.add_argument("--id", type=int)
    run.add_argument("--prompt")
    run.add_argument("--project")
    run.add_argument("--cwd", default=".")
    run.add_argument("--mode", choices=["read", "write", "deploy"], default="write")
    run.add_argument("--worker", choices=WORKER_CHOICES, default="gemini")
    run.add_argument("--ownership", action="append", default=[])
    run.add_argument("--dry-run", action="store_true")

    job_sub.add_parser("status")

    gate_parser = sub.add_parser("gate")
    gate_sub = gate_parser.add_subparsers(dest="gate_command", required=True)
    gate_sub.add_parser("list")
    answer = gate_sub.add_parser("answer")
    answer.add_argument("job_id", type=int)
    answer.add_argument("answer")

    sub.add_parser("status")
    log = sub.add_parser("log")
    log.add_argument("job_id", type=int)
    retry = sub.add_parser("retry")
    retry.add_argument("job_id", type=int)

    worker = sub.add_parser("worker")
    worker.add_argument("job_id", type=int)
    worker.add_argument("worker", choices=WORKER_CHOICES)

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--max-workers", type=int, default=2)
    dispatch.add_argument("--forever", action="store_true")
    dispatch.add_argument("--interval", type=float, default=5)
    dispatch.add_argument("--worker-timeout", type=float)
    dispatch.add_argument("--stale-lock-seconds", type=float, default=runner.DEFAULT_STALE_LOCK_SECONDS)

    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_sub.add_parser("list")
    project_create = project_sub.add_parser("create")
    project_create.add_argument("name")
    project_create.add_argument("--work-root", default=DEFAULT_WORK_ROOT)

    metrics = sub.add_parser("metrics")
    metrics_sub = metrics.add_subparsers(dest="metrics_command", required=True)
    metrics_sub.add_parser("show")

    template = sub.add_parser("template")
    template_sub = template.add_subparsers(dest="template_command", required=True)
    template_sub.add_parser("list")
    approve = template_sub.add_parser("approve")
    approve.add_argument("template_id", type=int)
    propose = template_sub.add_parser("propose")
    propose.add_argument("template_id", type=int)
    propose.add_argument("--minimum-samples", type=int, default=3)

    args = parser.parse_args()
    db_path = Path(args.db)
    init_db(db_path)

    if args.command == "job":
        return _job(args, db_path)
    if args.command == "gate":
        return _gate(args, db_path)
    if args.command == "status":
        return _status(db_path)
    if args.command == "log":
        for line in orchestrator.log_lines(args.job_id):
            print(line)
        return 0
    if args.command == "retry":
        print(orchestrator.retry_job(args.job_id, db_path=db_path))
        return 0
    if args.command == "worker":
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE jobs SET worker = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (args.worker, args.job_id),
            )
        print(f"job {args.job_id} worker={args.worker}")
        return 0
    if args.command == "dispatch":
        if args.forever:
            runner.run_forever(
                db_path,
                max_workers=args.max_workers,
                interval_seconds=args.interval,
                worker_timeout_seconds=args.worker_timeout,
                stale_lock_seconds=args.stale_lock_seconds,
            )
            return 0
        completed = runner.run_once(
            db_path,
            max_workers=args.max_workers,
            worker_timeout_seconds=args.worker_timeout,
            stale_lock_seconds=args.stale_lock_seconds,
        )
        print(" ".join(str(job_id) for job_id in completed) if completed else "no queued jobs")
        return 0
    if args.command == "project":
        return _project(args, db_path)
    if args.command == "metrics":
        with connect(db_path) as conn:
            print(json.dumps(evaluator.metrics_summary(conn), indent=2, ensure_ascii=False))
        return 0
    if args.command == "template":
        return _template(args, db_path)
    return 1


def _job(args, db_path: Path) -> int:
    if args.job_command == "add":
        if args.dry_run:
            check = orchestrator.dry_run_check(args.project, args.cwd, args.mode, args.c, args.o, args.i, db_path)
            print(json.dumps(check, indent=2, ensure_ascii=False))
            return 0
        job_id = orchestrator.queue_job(
            args.project,
            args.prompt,
            args.cwd,
            args.mode,
            args.worker,
            args.c,
            args.o,
            args.i,
            args.ownership,
            db_path,
        )
        print(job_id)
        return 0
    if args.job_command == "run":
        if args.prompt:
            if not args.project:
                raise SystemExit("--project is required with --prompt")
            if args.dry_run:
                check = orchestrator.dry_run_check(args.project, args.cwd, args.mode, 0.8, 0.8, 0.2, db_path)
                print(json.dumps(check, indent=2, ensure_ascii=False))
                return 0
            job_id = orchestrator.queue_job(
                args.project,
                args.prompt,
                args.cwd,
                args.mode,
                args.worker,
                ownership_paths=args.ownership,
                db_path=db_path,
            )
        else:
            job_id = args.id
        if job_id is None:
            dispatched = orchestrator.dispatch_next(db_path)
            print("no queued jobs" if dispatched is None else dispatched)
        else:
            orchestrator.run_job(job_id, db_path=db_path)
            print(job_id)
        return 0
    if args.job_command == "status":
        return _status(db_path)
    return 1


def _gate(args, db_path: Path) -> int:
    with connect(db_path) as conn:
        if args.gate_command == "list":
            for row in gate.list_open_gates(conn):
                print(f"#{row['job_id']} {row['project']} {row['reason']}: {row['question']}")
            return 0
        if args.gate_command == "answer":
            gate.answer_gate(conn, args.job_id, args.answer)
            print(f"queued {args.job_id}")
            return 0
    return 1


def _status(db_path: Path) -> int:
    for job in orchestrator.list_jobs(db_path):
        print(f"#{job['id']} {job['project']} {job['status']} worker={job['worker']} safe={job['safe_score']:.3f}")
    return 0


def _project(args, db_path: Path) -> int:
    if args.project_command == "list":
        projects = orchestrator.project_list(db_path)
        if not projects:
            print("no projects")
            return 0
        for proj in projects:
            parts = [
                f"{proj['project']}",
                f"total={proj['total']}",
                f"queued={proj['queued']}",
                f"running={proj['running']}",
                f"succeeded={proj['succeeded']}",
                f"failed={proj['failed']}",
                f"waiting={proj['waiting_human']}",
                f"updated={proj['last_updated']}",
            ]
            print("  ".join(parts))
        return 0
    if args.project_command == "create":
        from fyws.gateway import create_project
        path = create_project(args.name, args.work_root)
        print(path)
        return 0
    return 1


def _template(args, db_path: Path) -> int:
    with connect(db_path) as conn:
        if args.template_command == "list":
            rows = conn.execute("SELECT * FROM prompt_templates ORDER BY name, version").fetchall()
            for row in rows:
                print(f"#{row['id']} {row['name']} v{row['version']} {row['status']}")
            return 0
        if args.template_command == "approve":
            evaluator.approve_template(conn, args.template_id)
            print(f"approved {args.template_id}")
            return 0
        if args.template_command == "propose":
            proposal = evaluator.propose_improvement(conn, args.template_id, args.minimum_samples)
            print("not enough samples" if proposal is None else f"draft {proposal}")
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
