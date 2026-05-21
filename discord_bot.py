from __future__ import annotations

import argparse
import asyncio
import os

from fyws import gate, gateway, orchestrator, runner
from fyws.db import DEFAULT_DB_PATH, connect


def main() -> int:
    parser = argparse.ArgumentParser(description="FYWS Discord gateway helper")
    parser.add_argument(
        "message",
        nargs="?",
        help="'<project>: <instruction>', '<worker> <project>: <instruction>', 'status', 'log <job-id>', 'gate', or 'answer <job-id> <text>'",
    )
    parser.add_argument("--work-root", default=os.environ.get("FYWS_WORK_ROOT", gateway.DEFAULT_WORK_ROOT))
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--serve", action="store_true", help="run a Discord bot using discord.py")
    parser.add_argument("--token", default=os.environ.get("DISCORD_TOKEN"))
    parser.add_argument("--channel-id", type=int, default=_env_int("FYWS_DISCORD_CHANNEL_ID"))
    parser.add_argument("--run-jobs", action="store_true", help="run queued jobs in the Discord process")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument(
        "--message-content-intent",
        action="store_true",
        default=_env_bool("FYWS_DISCORD_MESSAGE_CONTENT_INTENT", False),
        help="request Discord's privileged message content intent instead of polling channel history",
    )
    parser.add_argument("--message-poll-interval", type=float, default=2)
    parser.add_argument(
        "--allow-self-messages",
        action="store_true",
        help="process messages sent by this bot; intended only for live E2E checks",
    )
    args = parser.parse_args()

    if args.serve:
        return serve_discord(
            args.token,
            args.channel_id,
            args.work_root,
            args.db,
            run_jobs=args.run_jobs,
            max_workers=args.max_workers,
            interval_seconds=args.interval,
            message_content_intent=args.message_content_intent,
            message_poll_interval=args.message_poll_interval,
            allow_self_messages=args.allow_self_messages,
        )

    if not args.message:
        parser.print_help()
        return 0

    message = args.message.strip()
    if message == "status":
        jobs = orchestrator.list_jobs(args.db)
        if not jobs:
            print("no jobs")
            return 0
        for job in jobs:
            print(gateway.format_completion(job["id"], job["project"], job["status"]))
        return 0
    if message == "gate":
        with connect(args.db) as conn:
            rows = gate.list_open_gates(conn)
            if not rows:
                print("no open gates")
                return 0
            for row in rows:
                print(gateway.format_gate(row["job_id"], row["project"], row["reason"], row["question"]))
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

    queued = gateway.queue_from_message(message, args.work_root, db_path=args.db)
    print(gateway.format_queued(queued))
    return 0


def serve_discord(
    token: str | None,
    channel_id: int | None,
    work_root: str,
    db_path: str,
    run_jobs: bool = False,
    max_workers: int = 2,
    interval_seconds: float = 5,
    message_content_intent: bool = False,
    message_poll_interval: float = 2,
    allow_self_messages: bool = False,
) -> int:
    if not token:
        raise SystemExit("DISCORD_TOKEN or --token is required")
    try:
        import discord
    except ImportError as exc:
        raise SystemExit("discord.py is required for --serve. Install it outside FYWS if you need the live bot.") from exc

    intents = discord.Intents.default()
    intents.message_content = message_content_intent
    client = discord.Client(intents=intents)
    notify_channel = {"value": None}
    seen_message_id = {"value": None}

    @client.event
    async def on_ready():
        print(f"FYWS Discord gateway connected as {client.user}")
        if run_jobs:
            client.loop.create_task(_runner_loop(client, notify_channel, channel_id, db_path, max_workers, interval_seconds))
        if not message_content_intent and channel_id is not None:
            client.loop.create_task(
                _message_poll_loop(
                    client,
                    notify_channel,
                    seen_message_id,
                    channel_id,
                    work_root,
                    db_path,
                    message_poll_interval,
                    allow_self_messages,
                )
            )

    @client.event
    async def on_message(message):
        if not message_content_intent:
            return
        if message.author == client.user and not allow_self_messages:
            return
        if channel_id is not None and message.channel.id != channel_id:
            return
        notify_channel["value"] = message.channel
        response = handle_message(message.content, work_root, db_path)
        if response:
            await message.channel.send(response)

    client.run(token)
    return 0


async def _message_poll_loop(
    client,
    notify_channel: dict,
    seen_message_id: dict,
    channel_id: int,
    work_root: str,
    db_path: str,
    interval: float,
    allow_self_messages: bool,
) -> None:
    channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
    notify_channel["value"] = channel
    if seen_message_id["value"] is None:
        async for message in channel.history(limit=1):
            seen_message_id["value"] = message.id
            break

    while not client.is_closed():
        messages = []
        after = None
        if seen_message_id["value"] is not None:
            after = _discord_object(client, seen_message_id["value"])
        async for message in channel.history(limit=20, after=after, oldest_first=True):
            messages.append(message)
        for message in messages:
            seen_message_id["value"] = max(seen_message_id["value"] or 0, message.id)
            if message.author == client.user and not allow_self_messages:
                continue
            response = handle_message(message.content, work_root, db_path)
            if response:
                await channel.send(response)
        await asyncio.sleep(interval)


def handle_message(message: str, work_root: str, db_path: str) -> str:
    text = message.strip()
    if text == "status":
        jobs = orchestrator.list_jobs(db_path)
        return "\n".join(gateway.format_completion(job["id"], job["project"], job["status"]) for job in jobs) or "no jobs"
    if text == "gate":
        with connect(db_path) as conn:
            rows = gate.list_open_gates(conn)
            return "\n".join(
                gateway.format_gate(row["job_id"], row["project"], row["reason"], row["question"]) for row in rows
            ) or "no open gates"
    if text.startswith("answer "):
        _, job_id, answer = text.split(maxsplit=2)
        with connect(db_path) as conn:
            gate.answer_gate(conn, int(job_id.lstrip("#")), answer)
        return f"queued #{job_id.lstrip('#')}"
    if text.startswith("log "):
        job_id = int(text.split(maxsplit=1)[1].lstrip("#"))
        summary = orchestrator.ARTIFACTS_DIR / str(job_id) / "summary.md"
        return summary.read_text(encoding="utf-8") if summary.exists() else f"summary for #{job_id} not found"
    if ":" in text:
        queued = gateway.queue_from_message(text, work_root, db_path=db_path)
        return gateway.format_queued(queued)
    return ""


async def _runner_loop(client, notify_channel: dict, channel_id: int | None, db_path: str, max_workers: int, interval: float) -> None:
    while not client.is_closed():
        notifications: list[str] = []

        def notify(job_id: int, project: str, status: str) -> None:
            notifications.append(format_job_notification(job_id, project, status, db_path))

        await asyncio.to_thread(runner.run_once, db_path, max_workers, notify)
        channel = notify_channel.get("value")
        if channel is None and channel_id is not None:
            channel = client.get_channel(channel_id)
        if channel is not None:
            for notification in notifications:
                await channel.send(notification)
        await asyncio.sleep(interval)


def format_job_notification(job_id: int, project: str, status: str, db_path: str) -> str:
    if status == "waiting_human":
        with connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT reason, question FROM human_requests
                WHERE job_id = ? AND status = 'open'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if row is not None:
            return gateway.format_gate(job_id, project, row["reason"], row["question"])
    return gateway.format_completion(job_id, project, status)


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _discord_object(client, object_id: int):
    import discord

    return discord.Object(id=object_id)


if __name__ == "__main__":
    raise SystemExit(main())
