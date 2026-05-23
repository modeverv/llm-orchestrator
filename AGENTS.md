# Agents.md — FYWS

> Finish Your Work, Stability, and Go Home Quickly

このファイルはClaude Codeがこのリポジトリで作業するときに必ず読む指示書。
実装前に必ずこのファイルと `PLAN.md` を読むこと。

今回は全てのgit 操作して良い。任意なタイミングでbranch切って作業しても良い。辛くなったらgitで過去に戻ってやり直しても良い。

## オートパイロット時の行動規範

- コンテキストが尽きる前に必ずsummary.mdを更新して止まれ
- 判断が必要になったらコードを書くな。human_gateを開けて止まれ
- 「たぶんこれで良い」という判断を自分でするな
- 完了できなくても、現在地を正確に記録して止まることが正解

## 環境
- computer useなどを使って良い。
- discordはブラウザで開いたらすぐに使えるようにしてある 
- discordにはチャンネルとかをllmと私ようで作って良い。APIキーとかも払い出して良い。(.envに書いておいてね)

## Codexアプリへの引き継ぎ

現在のリポジトリは Phase 1〜6 のMVP実装済み。`python -m pytest -q` は27件通過済み。
ただし実運用品質としては `PLAN.md` の「MVP後の未成熟ポイント」を次の作業契約にすること。

優先順位:

1. 実Discord接続E2E: `discord.py`, `DISCORD_TOKEN`, `FYWS_DISCORD_CHANNEL_ID`, `python discord_bot.py --serve --run-jobs`
2. 実worker E2E: Gemini CLI / Claude CLI で実repoを変更し、artifactsとstatus遷移を確認
3. `summary.md` 品質改善: events/diff/verification/job_events を固定スキーマへ反映
4. `ACCEPTANCE.md` パース: safe(T), mode, ownership paths をproject defaultから読む
5. 長時間runner運用: stale lock, timeout/cancel, resume, untracked file検査

フルアクセス環境で最初に実行する確認:

```bash
git status --short
python -m pytest -q
python -m py_compile cli.py discord_bot.py fyws/*.py fyws/workers/*.py
python discord_bot.py --help
python cli.py --help
```

未成熟ポイントを「完成済み」と扱わないこと。MVPとして使える範囲と、実運用検証が必要な範囲を分けて進めること。

---

## プロジェクトの思想

**LLMは探索機である。制御フローは人間が持つ。**

- オーケストレーション（何をいつ実行するか）は決定論的なPythonで行う
- LLM（GeminiやClaude）はworkerとして推論だけを担う
- セッション状態はLLMに持たせない。SQLiteとartifactsが状態の本体
- Hermesや重いフレームワークは使わない。Python + SQLite + CLI呼び出しのみ

---

## 非交渉ルール（絶対に守ること）

1. **状態はSQLiteに持たせる** — LLMセッションに状態を依存させない
2. **workerは差し替え可能にする** — `WorkerBase`を継承し同一インターフェースを守る
3. **lockなしでwriteしない** — `locks`テーブルを必ず経由する
4. **human_gate approveなしでprompt_templateを`active`にしない**
5. **所有範囲外の変更を検出したらfailにする** — `git diff --name-only`で検査
6. **WALモードを前提にする** — 並列jobのSQLite同時書き込みに対応
7. **外部ライブラリは最小限** — stdlib + sqlite3 + subprocess が基本

---

## ディレクトリ構造

```
fyws/
├── AGENTS.md          # このファイル（必ず最初に読む）
├── PLAN.md            # 実装計画とフェーズ定義
├── ARCHITECTURE.md    # アーキテクチャ詳細
├── schema.sql         # SQLiteスキーマ（正）
├── fyws/              # Pythonパッケージ
│   ├── __init__.py
│   ├── db.py          # DB接続・初期化
│   ├── orchestrator.py # ジョブキュー・ルーティング
│   ├── lock.py        # lock取得・解放
│   ├── gate.py        # human_gate
│   ├── summarizer.py  # summary.md生成
│   ├── evaluator.py   # メトリクス記録・prompt改善提案
│   ├── gateway.py     # Discord等の外部入力 → job生成
│   ├── runner.py      # queued job dispatch loop
│   └── workers/
│       ├── base.py    # WorkerBase・WorkerResult
│       ├── gemini.py  # GeminiWorker
│       └── claude.py  # ClaudeWorker
├── cli.py             # エントリポイント（python cli.py job run など）
├── discord_bot.py     # Discord gateway / helper
├── artifacts/         # job成果物（gitignore）
│   └── <job-id>/
│       ├── prompt.md
│       ├── events.jsonl
│       ├── last_message.txt
│       ├── summary.md
│       └── context.md
└── jobs.sqlite3       # DB本体（gitignore）
```

---

## summary.md の固定スキーマ

summarizer.pyが生成するsummary.mdは必ずこの構造にする。
自由作文にしない。

```markdown
# Job Summary

## User Goal
## Repo / CWD
## Non-Negotiable Rules
## Files Changed
## Commands Run
## Decisions Made
## Current State
## Verification
## Blockers
## Next Action
```

---

## context.md の構成

次jobに渡すcontext.mdに含めるもの（フルトランスクリプトは渡さない）：

```
- AGENTS.md（このファイル）
- 関連するACCEPTANCE.md
- task.md（今回のタスク）
- 直前のsummary.md
- 関連するdiff.patch（あれば）
```

---

## workerの呼び出し規約

### Gemini CLI

```bash
gemini \
  -p "$(cat prompt.md)" \
  --output-format stream-json \
  --approval-mode yolo \
  --model gemini-2.5-pro
```

セッション継続時：

```bash
gemini \
  --resume latest \
  -p "$(cat next_prompt.md)" \
  --output-format stream-json \
  --approval-mode yolo
```

### Claude CLI

```bash
claude \
  --print \
  < prompt.md
```

---

## safe(T) 判定

jobをキューに入れる前に必ずsafe値を記録する。

```
safe(T) = C(T) × O(T) × (1 − I(T))

C: 終了条件の形式化可能度（0〜1）
O: 副作用の観測可能度（0〜1）
I: 暗黙知依存度（0〜1）
```

safe < 0.3 のjobはhuman_gateを必須にする。

---

## lock ルール

```
OK:
  - 別プロジェクトの並列実行
  - 同一プロジェクトのread-onlyジョブの並列実行
  - 別worktree / 別ブランチなら並列write

NG:
  - 同一working treeへの複数writeジョブ同時実行
  - deploy / DB変更を複数ジョブが同時実行
```

---

## human_gate の発動条件

以下のいずれかで `waiting_human` にする：

1. 同じjobが2回連続でfailedになった
2. safe(T) < 0.3
3. 所有範囲外の変更が検出された
4. workerが明示的に「判断が必要」と出力した

---

## prompt_template の更新フロー

```
LLMが改善案を生成 → status='draft' で保存
         ↓
人間がapprove → status='active' に変更
         ↓
古いversionは status='deprecated' に変更
```

approveなしで自動的にactiveにしてはいけない。

---

## 実装時の注意

- `schema.sql` を正とする。コードからDDLを生成しない
- テストは `tests/` に置く。最低限 lock / gate / worker のunit test
- `artifacts/` と `jobs.sqlite3` は `.gitignore` に入れる
- エラーは握りつぶさない。必ず `job_events` にevent_type='error'で記録する
