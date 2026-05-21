from __future__ import annotations

from pathlib import Path

from fyws.workers.base import WorkerResult
from fyws.workers.gemini import _extract_message


def test_worker_result_defaults_are_independent():
    first = WorkerResult(True, "", "events.jsonl")
    second = WorkerResult(True, "", "events.jsonl")
    first.out_of_scope_files.append("a.py")
    assert second.out_of_scope_files == []


def test_extract_gemini_candidate_message():
    event = {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}
    assert _extract_message(event) == "done"
