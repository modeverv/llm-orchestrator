from __future__ import annotations

from fyws.gateway import parse_project_message


def test_parse_project_message():
    parsed = parse_project_message("spobook: FAQページのCSS、レスポンシブ対応して")
    assert parsed.project == "spobook"
    assert parsed.instruction == "FAQページのCSS、レスポンシブ対応して"


def test_parse_project_message_with_japanese_quotes():
    parsed = parse_project_message("「clientA: 検索結果ページの表示速度改善して」")
    assert parsed.project == "clientA"
    assert parsed.instruction == "検索結果ページの表示速度改善して"
