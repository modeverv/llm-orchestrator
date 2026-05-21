# Job Summary

## User Goal
- Continue P0 real-environment E2E hardening on branch `codex/p0-e2e-hardening` until blocked or complete.

## Repo / CWD
- `/Users/seijiro/Sync/sync_work/llm-orchestrator`

## Non-Negotiable Rules
- Keep state in SQLite.
- Preserve worker interchangeability.
- Do not write without lock.
- Do not mark prompt templates active without human approval.
- Fail or gate on out-of-scope changes.
- Use WAL mode for concurrent SQLite writes.
- Keep dependencies minimal.

## Files Changed
- `discord_bot.py`: added non-privileged Discord channel polling fallback, optional message-content intent, message poll interval, and `--allow-self-messages` for live E2E.
- `fyws/db.py`: added SQLite timeout, busy timeout, connection init lock, and lock retry around PRAGMA/schema initialization.
- `fyws/gateway.py`: changed generated per-job acceptance file from `acceptance.md` to `task.acceptance.md` to avoid clobbering project `ACCEPTANCE.md` on case-insensitive filesystems.
- `fyws/orchestrator.py`: clears known artifact files before each run and reads `task.acceptance.md` for job-specific acceptance.
- `tests/test_gateway.py`: added concurrent DB init regressions and project `ACCEPTANCE.md` preservation test.
- `tests/test_orchestrator.py`: added stale artifact cleanup regression test.
- `PLAN.md`: recorded current P0 E2E results and remaining Claude auth blocker.
- `fyws/workers/claude.py`: added `--permission-mode acceptEdits` so non-interactive Claude worker runs can edit files.
- `tests/test_claude_worker.py`: added regression coverage for the Claude CLI command.
- `fyws/workers/codex.py`: added Codex CLI worker using `codex exec`.
- `tests/test_codex_worker.py`: added regression coverage for Codex worker command, missing executable handling, and nested message extraction.
- `cli.py`, `fyws/orchestrator.py`: added `codex` as a routable worker choice.
- `README.md`, `ARCHITECTURE.md`, `PLAN.md`: documented Codex worker support and E2E result.
- `fyws/gateway.py`, `discord_bot.py`: added Discord worker-prefix parsing and changed the gateway default work root to `~/work/001_work/by-llms`.

## Commands Run
- `git status --short`
- `ps aux | rg 'discord_bot.py|cli.py --db /tmp/fyws-p0-e2e|gemini|claude --print'`
- `python -m pytest tests/test_discord_bot.py tests/test_gateway.py tests/test_runner.py tests/test_discord_notifications.py -q`
- `python -m pytest tests/test_gateway.py tests/test_orchestrator.py tests/test_discord_bot.py tests/test_runner.py tests/test_discord_notifications.py -q`
- `python -m pytest -q`
- `python -m py_compile cli.py discord_bot.py fyws/*.py fyws/workers/*.py`
- `python discord_bot.py --help`
- `python cli.py --help`
- `python discord_bot.py --db /tmp/fyws-discord-live.sqlite3 --serve --run-jobs --max-workers 2 --interval 2 --message-poll-interval 2 --allow-self-messages`
- Discord live E2E send/read scripts using `discord.py` and the existing `DISCORD_TOKEN` / `FYWS_DISCORD_CHANNEL_ID`.

## Decisions Made
- Do not require Discord privileged Message Content Intent by default; use channel history polling when `--message-content-intent` is not requested.
- Keep `--allow-self-messages` explicit and E2E-only because processing bot-authored commands is useful for automated live checks but should not be the normal mode.
- Treat Claude worker as operational after re-authentication and `acceptEdits` permission mode.
- Record Gemini/Discord live success separately from Claude auth failure.

## Current State
- Tests: `31 passed`.
- Py compile: passed for `cli.py`, `discord_bot.py`, `fyws/*.py`, and `fyws/workers/*.py`.
- Help commands: `python discord_bot.py --help` and `python cli.py --help` passed.
- Live Discord/Gemini E2E: two real repos, `fyws-live-gemini-a` and `fyws-live-gemini-b`, both reached `queued → running → succeeded` in `/tmp/fyws-discord-live.sqlite3`.
- Live Claude E2E: real repo `fyws-live-claude` reached `queued → running → succeeded` in `/tmp/fyws-claude-live.sqlite3` and changed `notes.txt`.
- Live Codex E2E: real repo `fyws-live-codex` reached `queued → running → succeeded` in `/tmp/fyws-codex-live.sqlite3` and changed `notes.txt`.
- Discord history showed queue replies and succeeded notifications.
- Fixture repos under `/Users/seijiro/work/fyws-live-gemini-a` and `/Users/seijiro/work/fyws-live-gemini-b` are intentionally dirty from E2E (`notes.txt`, `task.md`, `task.acceptance.md`).

## Verification
- `/tmp/fyws-discord-live.sqlite3` jobs:
  - `#1 fyws-live-gemini-a succeeded worker=gemini safe_score=0.512 attempts=1`
  - `#2 fyws-live-gemini-b succeeded worker=gemini safe_score=0.512 attempts=1`
- `/tmp/fyws-claude-live.sqlite3` jobs:
  - `#1 fyws-live-claude succeeded worker=claude safe_score=0.512 attempts=1`
- `/tmp/fyws-codex-live.sqlite3` jobs:
  - `#1 fyws-live-codex succeeded worker=codex safe_score=0.512 attempts=1`
- Discord-style worker-prefixed messages now queue the selected worker:
  - `codex myproj1: ...`
  - `claude myproj2: ...`
  - `gemini myproj3: ...`
- `/tmp/fyws-worker-prefix.sqlite3` verification:
  - `myproj1` queued with `worker=codex` under `/Users/seijiro/work/001_work/by-llms/myproj1`
  - `myproj2` queued with `worker=claude` under `/Users/seijiro/work/001_work/by-llms/myproj2`
  - `myproj3` queued with `worker=gemini` under `/Users/seijiro/work/001_work/by-llms/myproj3`
- `notes.txt` contents:
  - `Gemini FYWS Discord live A ok.`
  - `Gemini FYWS Discord live B ok.`
- Discord history included:
  - `fyws-live-gemini-a #1 queued (safe=0.512)`
  - `fyws-live-gemini-b #2 queued (safe=0.512)`
  - `fyws-live-gemini-b #2 succeeded`
  - `fyws-live-gemini-a #1 succeeded`

## Blockers
- True user-authored Discord message testing was not performed through the browser because GUI posting would require action-time confirmation. Automated live E2E used bot-authored messages with `--allow-self-messages`.

## Next Action
- Optionally add a documented operator note for Discord deployments explaining `--message-content-intent` vs polling fallback.
