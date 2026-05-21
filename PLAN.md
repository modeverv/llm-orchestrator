# PLAN.md — FYWS 実装計画

## フェーズ概要

```
Phase 1: 動く骨格          → 1jobを手動で動かせる
Phase 2: lock と gate      → 安全に並列実行できる
Phase 3: summarizer        → セッション間の継続ができる
Phase 4: evaluator         → メトリクス記録とprompt改善ループ
Phase 5: CLI polish        → 日常的に使える仕上げ
Phase 6: Discord gateway   → 寝転びながら複数プロジェクトを並列で動かす
```

---

## Phase 1: 動く骨格

**Goal:** `python cli.py job run --prompt task.md --project myproject` で1jobが実際に動く

**Acceptance:**
- [x] `jobs.sqlite3` が自動生成される
- [x] jobのstatusが queued → running → succeeded/failed に遷移する
- [x] `artifacts/<job-id>/events.jsonl` に出力が保存される
- [x] `artifacts/<job-id>/last_message.txt` に最終出力が保存される
- [x] Gemini CLIが実際に呼ばれる（実Geminiでのコード書き換え確認は環境依存）

**実装対象:**
```
schema.sql          → 完成済み
fyws/db.py          → DB接続・init_db()
fyws/workers/base.py → WorkerBase・WorkerResult
fyws/workers/gemini.py → GeminiWorker（subprocess呼び出し）
fyws/orchestrator.py → queue_job()・run_job()（lockなし簡易版）
cli.py              → job add / job run / job status
```

**やらないこと（Phase 2以降）:**
- lockの取得（並列実行なし）
- human_gate
- summarizer
- テスト

---

## Phase 2: lock と gate

**Goal:** 複数jobを並列キューに積んでも安全に動く

**Acceptance:**
- [x] 同一projectへの同時writeがlockで防がれる
- [x] readジョブは並列実行できる
- [x] 2回連続failでwaiting_humanに遷移する
- [x] 所有範囲外の変更が検出されたらwaiting_humanになる
- [x] human_requestsテーブルにquestionが記録される
- [x] `python cli.py gate answer <job-id>` で再開できる

**実装対象:**
```
fyws/lock.py        → acquire_lock()・release_lock()・check_conflict()
fyws/gate.py        → open_gate()・answer_gate()・resume_from_gate()
fyws/orchestrator.py → dispatch_next()（lock付き）
                       ownership_check()（git diff --name-only）
cli.py              → gate answer / gate list
```

---

## Phase 3: summarizer

**Goal:** jobをまたいでコンテキストが引き継がれる

**Acceptance:**
- [x] job完了後に `artifacts/<job-id>/summary.md` が固定スキーマで生成される
- [x] 次jobのpromptに前jobのsummary.mdが自動的に含まれる
- [x] context.mdが生成される（AGENTS.md + task.md）
- [x] トークン上限到達時（last_messageで検知）に自動summarizeが走る（完了時summary生成に集約）

**実装対象:**
```
fyws/summarizer.py  → summarize()（Gemini -p で固定スキーマを要求）
                       build_context()（context.md生成）
fyws/orchestrator.py → token_limit_handler()
```

**固定スキーマ（再掲）:**
```
## User Goal / Repo / CWD / Non-Negotiable Rules /
## Files Changed / Commands Run / Decisions Made /
## Current State / Verification / Blockers / Next Action
```

---

## Phase 4: evaluator

**Goal:** メトリクスが蓄積されpromptが改善されていく

**Acceptance:**
- [x] job完了ごとに job_metrics が記録される
- [x] `python cli.py metrics show` でoutcome率・平均token・平均時間が見える
- [x] 同一テンプレートでN件蓄積後にdraft改善案が生成される
- [x] `python cli.py template approve <id>` でactiveになる
- [x] approveなしではactiveにならない

**実装対象:**
```
fyws/evaluator.py   → record_metrics()・analyze_template()・propose_improvement()
cli.py              → metrics show / template list / template approve
```

---

## Phase 5: CLI polish

**Goal:** 毎日の開発作業で実際に使える

**Acceptance:**
- [x] `fyws status` でキュー全体の状況が一覧できる
- [x] `fyws log <job-id>` でevents.jsonlを人間が読める形で表示できる
- [x] `fyws retry <job-id>` で失敗jobを再投入できる
- [x] worker切り替え（gemini→claude）が1コマンドでできる
- [x] `--dry-run` でlock確認とsafe値チェックだけできる

---

## Phase 6: Discord gateway

**Goal:** Discordに書くだけで複数プロジェクトのjobが並列で動く

**完成形のユーザー体験:**
```
犬さん（寝転びながら）:
  「spobook: FAQページのCSS、レスポンシブ対応して」
  「clientA: 検索結果ページの表示速度改善して」
  「clientB: 月次レポートのExcel出力バグ直して」

FYWS Bot:
  ✅ spobook #job-47 queued (safe=0.72)
  ✅ clientA #job-48 queued (safe=0.81)
  ✅ clientB #job-49 queued (safe=0.65)

  --- 75分後 ---

  ⚠️ clientA #job-48 human_gate
  「本番DBのインデックス追加が必要です。実行してよいですか？」

犬さん: 「OK」

FYWS Bot:
  ✅ spobook #job-47 succeeded
  ✅ clientA #job-48 succeeded
  ✅ clientB #job-49 succeeded
```

**Acceptance:**
- [x] Discordの指定チャンネルに `「<project>: <指示>」` と書けばjobが生成される
- [x] safe値とjob IDがDiscordに返ってくる
- [x] human_gateの質問がDiscordに通知される
- [x] Discordで答えるとjobが再開される
- [x] `status` と書けば全jobの状況が返ってくる
- [x] `log <job-id>` と書けばsummary.mdが返ってくる
- [x] `--run-jobs` でDiscord gatewayプロセス内からqueued jobをdispatchして完了通知できる

**実装対象:**
```
discord_bot.py      → Discord.py bot エントリポイント
fyws/gateway.py     → Discordメッセージ → job生成の変換層
                       「<project>: <指示>」のパース
                       acceptance.mdの自動生成（safe値計算含む）
                       human_gate通知の送信
                       job完了通知の送信
fyws/runner.py      → queued jobのdispatchループと完了通知
```

**work directory convention:**
```
~/work/
  <project-name>/
    AGENTS.md        ← プロジェクト固有のルール
    SITE_CONTEXT.md  ← システム固有の暗黙知（I値を下げるため）
    ACCEPTANCE.md    ← デフォルト検証条件テンプレート
```

Discordで `「<project>: <指示>」` と書いたとき、FYWSは
`~/work/<project>/` 配下のファイルを自動的にcontext.mdに含める。
プロジェクトのディレクトリを作ってAGENTS.mdを置くだけで認識される。

**Project Digits / ローカルLLM との統合（オプション）:**
```
Geminiトークン切れ検知
  → ローカルLLM（Project Digits）にフォールバック
  → summarizer はローカルで常時無料で回す
```

---

## 実装の順番と判断基準

各Phaseの中でファイルを作る順番：

```
1. まずテストを書く（Acceptance条件をコードに落とす）
2. base/interfaceを書く
3. 実装を書く
4. cli.pyから動かして確認する
```

迷ったら**一番薄い実装で動かすことを優先**する。
完璧な実装より動く骨格が先。

---

## 現在の状態

- [x] schema.sql 完成
- [x] AGENTS.md 完成
- [x] ARCHITECTURE.md 完成
- [x] PLAN.md 完成（Phase 6追加済み）
- [x] Phase 1 実装済み
- [x] Phase 2 実装済み
- [x] Phase 3 実装済み
- [x] Phase 4 実装済み
- [x] Phase 5 実装済み
- [x] Phase 6 実装済み（discord.pyはoptional依存。未導入環境ではhelperとして動作）
