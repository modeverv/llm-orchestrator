from __future__ import annotations

import json
import re
from pathlib import Path


SUMMARY_SECTIONS = [
    "User Goal",
    "Repo / CWD",
    "Non-Negotiable Rules",
    "Files Changed",
    "Commands Run",
    "Decisions Made",
    "Current State",
    "Verification",
    "Blockers",
    "Next Action",
]

DEFAULT_NON_NEGOTIABLE_RULES = [
    "State lives in SQLite, writes require locks, workers stay replaceable, human_gate controls unsafe judgment, and ownership paths must be enforced."
]


def fixed_empty_summary() -> str:
    lines = ["# Job Summary", ""]
    for section in SUMMARY_SECTIONS:
        lines.extend([f"## {section}", "- Not recorded.", ""])
    return "\n".join(lines).rstrip() + "\n"


def summarize(
    artifact_dir: str | Path,
    user_goal: str,
    cwd: str,
    status: str,
    worker_message: str,
    files_changed: list[str] | None = None,
    verify_outputs: list[str] | None = None,
    gate_reason: str | None = None,
    job_events: list[str] | None = None,
    agents_path: str | Path | None = None,
) -> Path:
    artifact = Path(artifact_dir)
    summary_path = artifact / "summary.md"
    decisions, next_action = _extract_decisions_and_next_action(worker_message)
    sections = {
        "User Goal": [_clip_line(user_goal)],
        "Repo / CWD": [cwd],
        "Non-Negotiable Rules": _non_negotiable_rules(agents_path),
        "Files Changed": files_changed or [],
        "Commands Run": _commands_from_events(artifact / "events.jsonl"),
        "Decisions Made": decisions,
        "Current State": [status, *(job_events or [])],
        "Verification": _verification_lines(verify_outputs),
        "Blockers": [gate_reason] if gate_reason else [],
        "Next Action": next_action,
    }
    lines = ["# Job Summary", ""]
    for section in SUMMARY_SECTIONS:
        lines.append(f"## {section}")
        values = [value for value in sections.get(section, []) if value]
        if values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("- Not recorded.")
        lines.append("")
    content = "\n".join(lines).rstrip() + "\n"
    summary_path.write_text(content, encoding="utf-8")
    return summary_path


def build_context(
    artifact_dir: str | Path,
    agents_path: str | Path,
    task_path: str | Path,
    previous_summary_path: str | Path | None = None,
    acceptance_path: str | Path | None = None,
    acceptance_title: str = "ACCEPTANCE.md",
    diff_path: str | Path | None = None,
    site_context_path: str | Path | None = None,
) -> Path:
    artifact = Path(artifact_dir)
    context_path = artifact / "context.md"
    parts: list[str] = []
    _append_file(parts, "AGENTS.md", agents_path)
    if site_context_path:
        _append_file(parts, "SITE_CONTEXT.md", site_context_path)
    if acceptance_path:
        _append_file(parts, acceptance_title, acceptance_path)
    _append_file(parts, "task.md", task_path)
    if previous_summary_path:
        _append_file(parts, "previous summary.md", previous_summary_path)
    if diff_path:
        _append_file(parts, "diff.patch", diff_path)
    context_path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    return context_path


def token_limit_detected(message: str) -> bool:
    lower = message.lower()
    markers = ["token limit", "context length", "maximum context", "quota exceeded"]
    return any(marker in lower for marker in markers)


def _commands_from_events(events_path: Path) -> list[str]:
    if not events_path.exists():
        return []
    commands: list[str] = []
    for raw in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed = _parse_json_line(line)
        command = _command_from_event(parsed) if parsed else None
        if command is None and line.startswith("$ "):
            command = line[2:].strip()
        if command and command not in commands:
            commands.append(command)
    return commands


def _non_negotiable_rules(agents_path: str | Path | None) -> list[str]:
    if agents_path is None:
        return DEFAULT_NON_NEGOTIABLE_RULES
    path = Path(agents_path)
    if not path.exists():
        return DEFAULT_NON_NEGOTIABLE_RULES
    text = path.read_text(encoding="utf-8", errors="replace")
    section = _extract_agents_section(
        text,
        [
            "non-negotiable rules",
            "non negotiable rules",
            "非交渉ルール",
            "絶対に守ること",
        ],
    )
    if section:
        return section
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return [_clip_line(line)]
    return DEFAULT_NON_NEGOTIABLE_RULES


def _extract_agents_section(text: str, headings: list[str]) -> list[str]:
    values: list[str] = []
    capture = False
    for raw in text.splitlines():
        stripped = raw.strip()
        normalized = stripped.lstrip("#").strip().lower()
        if stripped.startswith("#") and any(heading in normalized for heading in headings):
            capture = True
            continue
        if capture and stripped.startswith("#"):
            break
        if not capture or not stripped:
            continue
        cleaned = re.sub(r"^[-*]\s+", "", stripped)
        cleaned = re.sub(r"^\d+[.)]\s*", "", cleaned)
        if cleaned:
            values.append(_clip_line(cleaned))
        if len(values) >= 8:
            break
    return values


def _command_from_event(event: dict | None) -> str | None:
    if not event:
        return None
    for key in ("command", "cmd", "argv"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            return " ".join(str(part) for part in value)
    message = event.get("message")
    if isinstance(message, str) and message.startswith("$ "):
        return message[2:].strip()
    return None


def _verification_lines(verify_outputs: list[str] | None) -> list[str]:
    if verify_outputs is None:
        return []
    if not verify_outputs:
        return ["No verify commands were configured."]
    lines: list[str] = []
    for output in verify_outputs:
        first_line = output.splitlines()[0] if output.splitlines() else output
        lines.append(_clip_line(first_line))
    return lines


def _extract_decisions_and_next_action(worker_message: str) -> tuple[list[str], list[str]]:
    if not worker_message.strip():
        return [], []
    decisions = _extract_sectionish_lines(worker_message, ["decisions made", "decisions", "変更内容", "実施内容"])
    next_action = _extract_sectionish_lines(worker_message, ["next action", "next steps", "次の作業", "次アクション"])
    if not decisions:
        decisions = [_clip_line(worker_message.strip().splitlines()[0])]
    if not next_action:
        next_action = ["Review last_message.txt for the worker's full final note."]
    return decisions[:5], next_action[:5]


def _extract_sectionish_lines(text: str, headings: list[str]) -> list[str]:
    lines = text.splitlines()
    values: list[str] = []
    capture = False
    for raw in lines:
        stripped = raw.strip()
        normalized = stripped.rstrip(":").strip("# ").lower()
        if any(normalized == heading for heading in headings):
            capture = True
            continue
        if capture and stripped.startswith("#"):
            break
        if capture and not stripped:
            if values:
                break
            continue
        if capture:
            values.append(_clip_line(re.sub(r"^[-*]\s+", "", stripped)))
    return values


def _parse_json_line(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _clip_line(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 15].rstrip() + " ...[truncated]"


def _append_file(parts: list[str], title: str, path: str | Path) -> None:
    p = Path(path)
    if p.exists():
        parts.append(f"# {title}\n\n{p.read_text(encoding='utf-8')}")
