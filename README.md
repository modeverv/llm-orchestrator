# FYWS

Finish Your Work, Stability, and Go Home Quickly.

FYWS keeps orchestration deterministic in Python and stores state in SQLite. LLMs are replaceable workers; they do not own session state.

## Quick Start

```bash
python cli.py job add --project myproject --prompt task.md --cwd /path/to/repo --ownership path/to/edit.py
python cli.py job add --project myproject --prompt task.md --cwd /path/to/repo --worker codex --ownership path/to/edit.py
python cli.py job run
python cli.py dispatch --max-workers 2
python cli.py dispatch --max-workers 2 --worker-timeout 3600 --stale-lock-seconds 21600
python cli.py status
python cli.py log 1
python cli.py inspect 1
python cli.py project list
```

Discord gateway project directories default to `~/work/001_work/by-llms`. Worker-prefixed messages select the worker:

```text
codex myproj1: fizzbuzzを実装して
claude myproj2: fizzbuzzを実装して
gemini myproj3: fizzbuzzを実装して
```

Low `safe(T)` jobs are queued into `waiting_human`:

```bash
env $(cat .env | xargs) python discord_bot.py --serve --run-jobs
python cli.py gate list
python cli.py gate answer 1 "approved"
python cli.py job run --id 1
```

Run checks:

```bash
python -m pytest -q
python -m py_compile cli.py discord_bot.py fyws/*.py fyws/workers/*.py
```

## Discord Connection

Create a `.env` with the live bot token and target channel:

```bash
DISCORD_TOKEN=...
FYWS_DISCORD_CHANNEL_ID=1507081006107328677
FYWS_WORK_ROOT=$HOME/work/001_work/by-llms
```

Install `discord.py` in the runtime environment, then start the gateway and runner:

```bash
python -m pip install discord.py
env $(cat .env | xargs) python discord_bot.py --serve --run-jobs --worker-timeout 3600 --stale-lock-seconds 21600
```

By default FYWS polls the configured channel history, so it can run without Discord's privileged Message Content Intent. If you enable that intent in the Discord developer portal, add `--message-content-intent`.

## Discord Commands

Start the bot:

```bash
env $(cat .env | xargs) python discord_bot.py --serve --run-jobs
```

Messages sent in the configured channel are processed as commands:

| Message | Description |
|---|---|
| `<project>: <instruction>` | Queue a job on the default worker (gemini) |
| `codex <project>: <instruction>` | Queue a job on Codex CLI |
| `claude <project>: <instruction>` | Queue a job on Claude CLI |
| `gemini <project>: <instruction>` | Queue a job on Gemini CLI |
| `projects` | List project directories with job counts |
| `status` | List all jobs with their current status |
| `gate` | List jobs waiting for human input |
| `answer <job-id> <text>` | Answer a human gate and requeue the job |
| `log <job-id>` | Show `summary.md`, falling back to `events.jsonl` or `last_message.txt` |

Examples:

```text
myproject: fizzbuzzを実装してください
claude myproject: unit testも追加して
answer 3 approved
log 3
```

Projects must exist under the work root (`~/work/001_work/by-llms` by default) before sending a message.
Use `python cli.py project create <name>` to create a new project directory with a starter `AGENTS.md`.

## Minimal Services

Minimal systemd unit:

```ini
[Unit]
Description=FYWS Discord gateway
After=network-online.target

[Service]
WorkingDirectory=/Users/seijiro/Sync/sync_work/llm-orchestrator
EnvironmentFile=/Users/seijiro/Sync/sync_work/llm-orchestrator/.env
ExecStart=/usr/bin/python3 discord_bot.py --serve --run-jobs --worker-timeout 3600 --stale-lock-seconds 21600
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Minimal launchd plist:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.fyws.discord</string>
  <key>WorkingDirectory</key><string>/Users/seijiro/Sync/sync_work/llm-orchestrator</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>python3</string>
    <string>discord_bot.py</string>
    <string>--serve</string>
    <string>--run-jobs</string>
    <string>--worker-timeout</string>
    <string>3600</string>
    <string>--stale-lock-seconds</string>
    <string>21600</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DISCORD_TOKEN</key><string>replace-me</string>
    <key>FYWS_DISCORD_CHANNEL_ID</key><string>replace-me</string>
    <key>FYWS_WORK_ROOT</key><string>/Users/seijiro/work/001_work/by-llms</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

## Notes

- `schema.sql` is the source of truth for SQLite schema.
- Write jobs use the `locks` table before running workers.
- Supported workers are `gemini`, `claude`, and `codex`.
- Prompt templates can only become `active` via `template approve`.
- `discord_bot.py` works as a dependency-free gateway helper by default.
- Discord responses are split into 2000-character chunks before sending.
- `python discord_bot.py --serve --run-jobs` runs a live Discord gateway when `discord.py` is installed and `DISCORD_TOKEN` is set.
- Jobs stuck in `running` state from a previous crash are automatically requeued when `dispatch --forever` starts.
- Long-running dispatch can reap stale locks and stop hung workers with `--stale-lock-seconds` and `--worker-timeout`.
- Gemini resume is limited to the same job after a recorded Gemini session; separate jobs always use a fresh CLI session plus `context.md`.
