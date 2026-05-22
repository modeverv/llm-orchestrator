from __future__ import annotations

from fyws.verifier import parse_verify_commands, run_verify


def test_parse_verify_commands_from_code_block(tmp_path):
    acceptance = tmp_path / "ACCEPTANCE.md"
    acceptance.write_text(
        """# Acceptance

## Verify Commands

```bash
python -m pytest -q
# comment
python -m py_compile cli.py
```
""",
        encoding="utf-8",
    )

    assert parse_verify_commands(acceptance) == ["python -m pytest -q", "python -m py_compile cli.py"]


def test_parse_verify_commands_from_bullets(tmp_path):
    acceptance = tmp_path / "ACCEPTANCE.md"
    acceptance.write_text(
        """# Acceptance

## Verify Commands

- python -m pytest -q
- python cli.py --help
1. python discord_bot.py --help
""",
        encoding="utf-8",
    )

    assert parse_verify_commands(acceptance) == [
        "python -m pytest -q",
        "python cli.py --help",
        "python discord_bot.py --help",
    ]


def test_run_verify_missing_acceptance_is_ok(tmp_path):
    assert run_verify(tmp_path / "missing.md", tmp_path) == (True, [])


def test_run_verify_success_and_first_failure(tmp_path):
    acceptance = tmp_path / "ACCEPTANCE.md"
    acceptance.write_text(
        """# Acceptance

## Verify Commands

- python -c "print('first')"
- python -c "import sys; print('second'); sys.exit(2)"
- python -c "print('third')"
""",
        encoding="utf-8",
    )

    ok, outputs = run_verify(acceptance, tmp_path)

    assert not ok
    assert len(outputs) == 2
    assert "first" in outputs[0]
    assert "second" in outputs[1]
    assert "third" not in "\n".join(outputs)
