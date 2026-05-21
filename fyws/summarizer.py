from __future__ import annotations

import subprocess
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


def fixed_empty_summary() -> str:
    lines = ["# Job Summary", ""]
    for section in SUMMARY_SECTIONS:
        lines.extend([f"## {section}", "- Not recorded.", ""])
    return "\n".join(lines).rstrip() + "\n"


def summarize(artifact_dir: str | Path, user_goal: str, cwd: str, status: str, worker_message: str) -> Path:
    artifact = Path(artifact_dir)
    summary_path = artifact / "summary.md"
    content = fixed_empty_summary()
    content = content.replace("## User Goal\n- Not recorded.", f"## User Goal\n- {user_goal}")
    content = content.replace("## Repo / CWD\n- Not recorded.", f"## Repo / CWD\n- {cwd}")
    content = content.replace("## Current State\n- Not recorded.", f"## Current State\n- {status}")
    if worker_message:
        content = content.replace(
            "## Decisions Made\n- Not recorded.",
            "## Decisions Made\n- Worker final message captured in last_message.txt.",
        )
    summary_path.write_text(content, encoding="utf-8")
    return summary_path


def build_context(
    artifact_dir: str | Path,
    agents_path: str | Path,
    task_path: str | Path,
    previous_summary_path: str | Path | None = None,
    acceptance_path: str | Path | None = None,
    diff_path: str | Path | None = None,
) -> Path:
    artifact = Path(artifact_dir)
    context_path = artifact / "context.md"
    parts: list[str] = []
    _append_file(parts, "AGENTS.md", agents_path)
    if acceptance_path:
        _append_file(parts, "ACCEPTANCE.md", acceptance_path)
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


def summarize_with_gemini(events_path: str | Path, summary_path: str | Path) -> bool:
    prompt = (
        "Summarize this job into the exact FYWS schema, with all headings present. "
        "Do not add headings.\n\n"
        + Path(events_path).read_text(encoding="utf-8", errors="replace")
    )
    try:
        proc = subprocess.run(
            ["gemini", "-p", prompt, "--output-format", "text"],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    if proc.returncode != 0 or "# Job Summary" not in proc.stdout:
        return False
    Path(summary_path).write_text(proc.stdout, encoding="utf-8")
    return True


def _append_file(parts: list[str], title: str, path: str | Path) -> None:
    p = Path(path)
    if p.exists():
        parts.append(f"# {title}\n\n{p.read_text(encoding='utf-8')}")
