from __future__ import annotations

import re
import subprocess
from pathlib import Path


def parse_verify_commands(acceptance_path: str | Path) -> list[str]:
    path = Path(acceptance_path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^## Verify Commands\s*\n(.*?)(?=^##|\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    section = match.group(1)
    commands: list[str] = []
    for block in re.findall(r"```(?:bash|sh|shell)?\n(.*?)```", section, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                commands.append(line)
    if commands:
        return commands
    for line in section.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        if line:
            commands.append(line)
    return commands


def run_verify(acceptance_path: str | Path, cwd: str | Path) -> tuple[bool, list[str]]:
    commands = parse_verify_commands(acceptance_path)
    if not commands:
        return True, []
    outputs: list[str] = []
    for cmd in commands:
        proc = subprocess.run(cmd, shell=True, cwd=str(cwd), text=True, capture_output=True)
        output = f"$ {cmd}\n{proc.stdout}"
        if proc.returncode != 0:
            output += proc.stderr
            outputs.append(output)
            return False, outputs
        outputs.append(output)
    return True, outputs
