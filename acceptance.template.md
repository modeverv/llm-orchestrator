# job-template/acceptance.md
# 検証条件（job作成時に人間が記入する）

## safe(T) スコア

- C（終了条件の形式化可能度）: 0.0〜1.0
- O（副作用の観測可能度）: 0.0〜1.0
- I（暗黙知依存度）: 0.0〜1.0
- safe = C × O × (1 - I): （自動計算）

## 完了条件（機械的に検証できる形で書く）

- [ ] 条件1
- [ ] 条件2

## Verify Commands

```bash
# 終了コード0で成功とみなされる検証コマンドを書く
# 例: python -m pytest -q
# 例: python -m py_compile path/to/file.py
```

## 所有パス（このjobが変更してよいファイル）

```yaml
ownership:
  mode: write  # read / write / deploy
  paths:
    - path/to/file.py
    - path/to/other.py
```

## スコープ外（変更してはいけないもの）

- 例: 本番DBへの直接変更
- 例: このリストにないファイル

## human_gate必須条件

- safe < 0.3 の場合は自動でhuman_gate
- 上記以外で判断が必要な場合はworkerが明示する
