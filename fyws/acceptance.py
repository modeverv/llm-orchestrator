from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re


DEFAULT_MODE = "write"
DEFAULT_C_SCORE = 0.8
DEFAULT_O_SCORE = 0.8
DEFAULT_I_SCORE = 0.2
DEFAULT_OWNERSHIP_PATHS = ["."]


@dataclass(frozen=True)
class AcceptanceDefaults:
    mode: str = DEFAULT_MODE
    c_score: float = DEFAULT_C_SCORE
    o_score: float = DEFAULT_O_SCORE
    i_score: float = DEFAULT_I_SCORE
    ownership_paths: list[str] = field(default_factory=lambda: list(DEFAULT_OWNERSHIP_PATHS))


def parse_acceptance_defaults(acceptance_path: str | Path | None) -> AcceptanceDefaults:
    if acceptance_path is None:
        return AcceptanceDefaults()
    path = Path(acceptance_path)
    if not path.exists():
        return AcceptanceDefaults()

    text = path.read_text(encoding="utf-8")
    c_score = _parse_score(text, "C", DEFAULT_C_SCORE)
    o_score = _parse_score(text, "O", DEFAULT_O_SCORE)
    i_score = _parse_score(text, "I", DEFAULT_I_SCORE)
    mode = _parse_mode(text) or DEFAULT_MODE
    paths = _parse_ownership_paths(text) or list(DEFAULT_OWNERSHIP_PATHS)
    return AcceptanceDefaults(mode, c_score, o_score, i_score, paths)


def project_acceptance_path(cwd: str | Path) -> Path:
    return Path(cwd).resolve() / "ACCEPTANCE.md"


def requires_forced_human_gate(mode: str, task_text: str) -> str | None:
    if mode == "deploy":
        return "deploy_requires_human"

    text = task_text.lower()
    if _contains_any(text, _SECRET_MARKERS):
        return "secret_operation_requires_human"
    if _contains_any(text, _DB_MARKERS):
        return "db_change_requires_human"
    if _contains_any(text, _DEPLOY_MARKERS):
        return "deploy_requires_human"
    return None


def _parse_score(text: str, label: str, default: float) -> float:
    patterns = [
        rf"(?im)^\s*[-*]?\s*{re.escape(label)}\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        rf"(?im)^\s*{re.escape(label.lower())}_score\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = float(match.group(1))
        if 0 <= value <= 1:
            return value
    return default


def _parse_mode(text: str) -> str | None:
    match = re.search(r"(?im)^\s*mode\s*:\s*(read|write|deploy)\s*$", text)
    return match.group(1) if match else None


def _parse_ownership_paths(text: str) -> list[str]:
    paths: list[str] = []
    in_paths = False
    paths_indent: int | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if re.match(r"^paths\s*:\s*$", stripped):
            in_paths = True
            paths_indent = len(raw) - len(raw.lstrip(" "))
            continue
        if not in_paths:
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if stripped.startswith("- "):
            value = stripped[2:].strip().strip("'\"")
            if value:
                paths.append(value)
            continue
        if stripped and paths_indent is not None and indent <= paths_indent:
            break
    return paths


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


_DEPLOY_MARKERS = (
    "deploy",
    "deployment",
    "production deploy",
    "release to prod",
    "本番反映",
    "本番デプロイ",
    "デプロイ",
)

_DB_MARKERS = (
    "db migration",
    "database migration",
    "schema migration",
    "alter table",
    "drop table",
    "create index",
    "migrate db",
    "本番db",
    "db変更",
    "db 変更",
    "データベース変更",
    "マイグレーション",
)

_SECRET_MARKERS = (
    "secret",
    "api key",
    "apikey",
    "token rotation",
    "rotate token",
    "rotate key",
    "credential",
    "credentials",
    "秘密鍵",
    "apiキー",
    "api key",
    "トークン更新",
    "シークレット",
)
