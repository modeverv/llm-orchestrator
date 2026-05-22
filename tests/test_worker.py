from __future__ import annotations

from pathlib import Path

import pytest

from fyws.workers.base import WorkerResult
from fyws.workers import claude, codex, gemini
from fyws.workers.gemini import _extract_command, _extract_message, _iter_tool_calls


def test_worker_result_defaults_are_independent():
    first = WorkerResult(True, "", "events.jsonl")
    second = WorkerResult(True, "", "events.jsonl")
    first.out_of_scope_files.append("a.py")
    assert second.out_of_scope_files == []


def test_extract_gemini_candidate_message():
    event = {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}
    assert _extract_message(event) == "done"


def test_extract_gemini_function_call_command():
    event = {"functionCall": {"name": "run_shell_command", "args": {"command": "python -m pytest -q"}}}
    assert _extract_command(event) == "python -m pytest -q"


def test_extract_gemini_text_shell_prompt_line():
    event = {"text": "$ git status --short"}
    assert _extract_command(event) == "git status --short"


def test_extract_gemini_text_does_not_guess_command_prefixes():
    event = {"text": "Run: this is risky, do not proceed"}
    assert _extract_command(event) == ""


def test_iter_tool_calls_stops_at_depth_limit():
    value = {"a": {"b": {"c": {"d": {"e": {"f": {"functionCall": {"args": {"command": "too deep"}}}}}}}}}
    assert list(_iter_tool_calls(value, max_depth=3)) == []


@pytest.mark.parametrize("extract_fn", [gemini._extract_command, claude._extract_command, codex._extract_command])
@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"type": "tool_use", "input": {"command": "pytest -q"}}, "pytest -q"),
        ({"functionCall": {"args": {"command": "python -m pytest -q"}}}, "python -m pytest -q"),
        ({"tool_call": {"command": "git status --short"}}, "git status --short"),
        ({"type": "message", "content": "no command here"}, None),
    ],
)
def test_all_workers_extract_commands_consistently(extract_fn, event, expected):
    result = extract_fn(event)
    assert (result or None) == expected


def test_claude_iter_tool_calls_does_not_yield_duplicate_wrapped_call():
    event = {"tool_use": {"input": {"command": "ls"}}}
    assert list(claude._iter_tool_calls(event)) == [{"input": {"command": "ls"}}]
