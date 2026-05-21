# FYWS

Finish Your Work, Stability, and Go Home Quickly.

FYWS keeps orchestration deterministic in Python and stores state in SQLite. LLMs are replaceable workers; they do not own session state.

## Quick Start

```bash
python cli.py job add --project myproject --prompt task.md --cwd /path/to/repo --ownership path/to/edit.py
python cli.py job run
python cli.py status
python cli.py log 1
```

Low `safe(T)` jobs are queued into `waiting_human`:

```bash
python cli.py gate list
python cli.py gate answer 1 "approved"
python cli.py job run --id 1
```

Run checks:

```bash
python -m pytest -q
python -m py_compile cli.py discord_bot.py fyws/*.py fyws/workers/*.py
```

## Notes

- `schema.sql` is the source of truth for SQLite schema.
- Write jobs use the `locks` table before running workers.
- Prompt templates can only become `active` via `template approve`.
- `discord_bot.py` is currently a dependency-free gateway helper, not a connected Discord client.
