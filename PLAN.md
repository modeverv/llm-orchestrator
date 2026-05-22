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
~/work/001_work/by-llms/
  <project-name>/
    AGENTS.md        ← プロジェクト固有のルール
    SITE_CONTEXT.md  ← システム固有の暗黙知（I値を下げるため）
    ACCEPTANCE.md    ← デフォルト検証条件テンプレート
```

Discordで `「<project>: <指示>」` と書いたとき、FYWSは
`~/work/001_work/by-llms/<project>/` 配下のファイルを自動的にcontext.mdに含める。
プロジェクトのディレクトリを作ってAGENTS.mdを置くだけで認識される。

現在のDiscord gateway既定ルートは `~/work/001_work/by-llms`。workerを明示する場合は
`codex myproj1: ...` / `claude myproj2: ...` / `gemini myproj3: ...`
の形式で書く。worker prefixなしの `<project>: ...` はGemini既定。

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

---

## MVP後の未成熟ポイント

現在のPhase 1〜6は、標準ライブラリ中心のMVPとしてテスト済み。
ただし「実案件を長時間放置で回す」運用品質としては、以下を次に詰めること。

### P0: 実環境E2E

- [x] `discord.py` を導入した環境で `python discord_bot.py --serve --run-jobs` を実Discordチャンネルに接続して検証する
- [x] `DISCORD_TOKEN` と `FYWS_DISCORD_CHANNEL_ID` を使った実メッセージ往復を確認する
- [x] Gemini CLI実行で、実repoに対して `queued → running → succeeded/failed` と artifacts 生成を確認する
- [x] Claude CLI実行で、worker差し替えが実際に動くことを確認する
- [x] Codex CLI実行で、worker差し替えが実際に動くことを確認する
- [x] `~/work/001_work/001_work/by-llms/<project>/AGENTS.md`, `SITE_CONTEXT.md`, `ACCEPTANCE.md` を持つ実projectを2つ以上作り、並列dispatchを確認する

2026-05-22 P0実測:
- `FYWS_DISCORD_MESSAGE_CONTENT_INTENT` なしの環境ではDiscord privileged intentで起動失敗したため、指定チャンネル履歴polling fallbackを追加して接続確認済み。
- Discordチャンネルに2件投入し、`fyws-live-gemini-a` / `fyws-live-gemini-b` が並列で `queued → running → succeeded`。queue返信と完了通知もDiscord履歴で確認済み。
- Gemini CLIは2つの実repoで `notes.txt` を変更し、`artifacts/<job-id>/prompt.md`, `events.jsonl`, `last_message.txt`, `summary.md`, `context.md` を生成。
- 初回E2EでmacOS case-insensitive FS上の `acceptance.md` / `ACCEPTANCE.md` 衝突と、別DBでjob idが再利用された際のstale artifactを検出し修正済み。
- Claude CLIは再認証後に実repo `fyws-live-claude` で `notes.txt` を変更し、`queued → running → succeeded` を確認済み。`--print` 単体では書き込み許可待ちになったため、ClaudeWorkerは `--permission-mode acceptEdits` を付けて非対話editを許可する。
- Codex CLIは実repo `fyws-live-codex` で `notes.txt` を変更し、`queued → running → succeeded` を確認済み。CodexWorkerは `codex exec -C <cwd> --json --output-last-message <artifact>/last_message.txt --dangerously-bypass-approvals-and-sandbox -` を使う。

### P1: summary/context品質

- [ ] `summary.md` の各セクションに、実際の `events.jsonl`, `git diff`, `job_events`, verification結果を反映する
- [ ] token limit検知時に単なる完了summaryではなく、途中summary → 新context → retry/continue の流れを実装する
- [ ] `context.md` に含める `ACCEPTANCE.md` の優先順位を明確化する（project default vs job-specific）
- [ ] `diff.patch` が存在する場合の引き継ぎを、out-of-scope時だけでなく通常retry時にも検証する

### P1: safe(T) と ownership

- [x] `ACCEPTANCE.md` から `C/O/I`, mode, ownership paths をパースして job 作成時の既定値にする
- [x] Discord指示から自動生成する `acceptance.md` の所有範囲を `.` 既定ではなく、project defaultから安全に絞る
- [x] deploy / DB変更 / secret操作は safe値に関係なく human_gate にする
- [x] ownership checkを `git diff --name-only` だけでなく untracked file も含めて検査する（`git status --porcelain` で新規ファイルも拾う）
- [x] `worker_requires_human` の文字列マッチングを強化する（現状は固定キーワードのみ。LLMが想定外の表現を使うと素通りする）

2026-05-22 P1実装:
- `fyws/acceptance.py` を追加し、project `ACCEPTANCE.md` の `C/O/I`, `ownership.mode`, `ownership.paths` を job 作成時の既定値として読む。明示指定された CLI / gateway 引数は既定値より優先する。
- Discord gateway が生成する `task.acceptance.md` は project default の ownership paths を引き継ぎ、project default がない場合だけ `.` にフォールバックする。
- `mode=deploy`、または指示文に deploy / DB migration / secret 操作の明示語がある場合は、safe(T) が高くても `waiting_human` にする。
- ownership check は worker 実行直前の `git status --porcelain` をbaselineにし、worker後に増えた tracked/untracked 変更だけを ownership paths と照合する。
- `worker_requires_human` は日本語・英語の承認/確認/人間判断/without approval 系表現を正規表現で検出する。

### P1: runner/lock運用

- [x] runnerを長時間動かしたときの stale lock 回収ルールを実装する
- [x] 同一project read jobの並列とwrite job待機が期待通りになる統合テストを追加する
- [x] workerプロセスのtimeout/cancelを実装する（現状はハング時にプロセスが生きたまま詰まる。crash recoveryはプロセス死亡時にしか効かない）
- [x] `ClaudeWorker` をストリーミング化して token_in/out を取得する（現状は `--print` + blocking で tokens 常に None、長時間タスクで途中経過が見えない）
- [x] job中断後のresume方針を明確化する（Gemini `--resume latest` をいつ使うか）

2026-05-22 P1実装:
- stale lock は `locks.owner` の `host:pid` と `jobs.status` で判定する。jobがrunning以外、job行がない、または同一hostのowner pidが死んでいて `--stale-lock-seconds` を超えたlockをrunnerが回収する。別host ownerは生存確認できないため自動削除しない。
- runner はqueued先頭N件を機械的に投げず、既存lockと同一batch内の仮想lockを見て実行可能jobだけ選ぶ。同一project/cwdのreadは同時実行し、write/deployはread/write/deployが残る限り待機する。
- `dispatch` / Discord `--run-jobs` に `--worker-timeout` と `--stale-lock-seconds` を追加した。timeout時はworker process groupへSIGTERM、残存時SIGKILLを送り、eventsにerrorを記録する。
- ClaudeWorkerは `--print --permission-mode acceptEdits` のままstdout/stderrを逐次 `events.jsonl` に流し、JSON usageまたはテキストのtoken表記から `tokens_in/out` を拾う。
- resumeは同一Gemini jobの継続だけに限定する。`gemini_session_id` が記録済みでattempts>0の同一job再実行時だけ `--resume latest` を使い、別jobはsummary/contextで新セッションへ渡す。

### P2: evaluator/prompt改善

- [x] `propose_improvement()` を固定文のdraft生成ではなく、実metricsと失敗summaryを入力にしたLLM提案へ拡張する
- [x] prompt_templateのactive versionをjob作成時に自動選択する
- [x] template approve時に古いactiveをdeprecatedへ落とす挙動の統合テストを増やす

2026-05-22 P2実装:
- `propose_improvement()` は `job_metrics` と失敗jobの `summary.md` をLLM入力にして、返却された本文を次versionの `draft` として保存する。テストではproposer callableを差し替え、実CLIではGemini CLIを呼ぶ。
- job作成時にproject名のactive templateを優先し、なければ `default` のactive templateを自動選択して `jobs.prompt_template_id` に記録する。
- template approve時は同名の旧activeのみを `deprecated` に落とし、別名templateのactiveは維持する統合テストを追加した。

### P2: 運用UX

- [x] `fyws project create/list` を追加して `~/work/001_work/by-llms/<project>` を管理する（Discord `projects` コマンドも対応済み）
- [x] `cli project list`（jobsテーブルベース）と Discord `projects`（FSベース）の表示を統一する（jobがないプロジェクトがCLI側に出ない不整合）
- [x] Discord応答の2000文字制限を処理する（長いsummaryや大量jobが無言で切れる）
- [x] `fyws inspect <job-id>` でDB状態、artifacts、summary、diff、gateをまとめて表示する
- [x] `discord_bot.py log <job-id>` がsummary未生成時にevents/last_messageへフォールバックする
- [x] READMEに実Discord接続手順と最小systemd/launchd運用例を書く

2026-05-22 P2運用UX実装:
- `gateway.list_projects()` をFSディレクトリとDB job統計の共通ソースにし、CLI `project list` とDiscord `projects` が同じ `format_projects()` 表示を使う。jobがまだないprojectは `total=0` として表示される。
- Discord live送信は `split_discord_messages()` で2000文字以内に分割して送る。`status`、`projects`、長い `log`、完了通知のすべてが同じ送信経路を通る。
- `python cli.py inspect <job-id>` を追加し、jobs行、artifacts有無/サイズ、human gate、job_events、summary/diff/last_messageを1画面に出す。
- `discord_bot.py log <job-id>` / live `log <job-id>` は `summary.md` がなければ `events.jsonl`、それもなければ `last_message.txt` を返す。
- READMEに実Discord接続手順、polling fallbackとMessage Content Intentの切り替え、最小systemd/launchd例を追加した。

---

## P3: 品質・技術的負債

### P3-A: summarizer 実装（最優先）

現状の `summarize()` は 10 セクション中 3 つしか埋めない実質スタブ。
`summarize_with_gemini()` は定義されているが **どこからも呼ばれていない**（デッドコード）。
summary が空だと evaluator の失敗分析も context 引き継ぎも機能しない。

- [x] `summarize()` に `files_changed` 引数を追加し、`git_status_paths()` の差分から `## Files Changed` を埋める
- [x] `events.jsonl` を読んでコマンド行を `## Commands Run` に反映する
- [x] `result.last_message` から `## Decisions Made` と `## Next Action` を抽出して埋める
- [x] `verifier` の出力（`verify_outputs`）を `run_job()` から `summarize()` に渡し `## Verification` に反映する
- [x] gate reason がある場合に `## Blockers` を埋める
- [x] `summarize_with_gemini()` を `run_job()` の完了時に呼ぶか、上記で LLM 不要になったら削除する

### P3-B: token limit 自動ハンドリング

`token_limit_detected()` が定義されているが `run_job()` から呼ばれていない。
トークン枯渇時に中途半端な成功扱いになっている。

- [x] `run_job()` の result 取得直後に `token_limit_detected(result.last_message)` を呼ぶ
- [x] 検知時は human_gate を開いて「トークン上限に達しました。新セッションで続行しますか？」と通知する
- [ ] （発展）自動で新 job を作り summary を context として引き継ぐ auto-continue を実装する

### P3-C: verifier のテスト追加

`fyws/verifier.py` は P1 で追加された新モジュールだがテストファイルがない。

- [x] `tests/test_verifier.py` を作成し、`parse_verify_commands()` と `run_verify()` をテストする
  - ACCEPTANCE.md のパースパターン（コードブロック形式・箇条書き形式）
  - コマンド成功時（全て True）、最初の失敗で早期リターン
  - ACCEPTANCE.md が存在しない場合は `(True, [])` を返す

### P3-D: `dispatch_next()` の整理

`fyws/orchestrator.py` の `dispatch_next()` は lock-aware でないシングルジョブ版で、
runner の `_runnable_job_ids()` と役割が重複している。

- [x] CLI から `dispatch_next()` が実際に呼ばれているか確認する
- [x] 呼ばれていなければ削除、または `run_once()` に統一する

### P3-E: ARTIFACTS_DIR の設定可能化

`orchestrator.py` と `evaluator.py` の `ARTIFACTS_DIR` がソースコード相対にハードコードされている。
DB ファイルの場所を変えると不整合が起きる。

- [x] `FYWS_ARTIFACTS_DIR` 環境変数で上書きできるようにする（デフォルトは現在と同じ）
- [x] `db_path` と同じディレクトリ配下の `artifacts/` をデフォルトにする案も検討する

### P3-F: artifact の自動整理

`artifacts/<job-id>/` が永続的に蓄積され、長期運用で肥大化する。

- [x] `python cli.py artifacts prune --keep-days N` コマンドを追加する
  - succeeded/failed かつ `N` 日以上前の job の artifacts を削除対象にする
  - `--dry-run` で削除対象を表示するだけにするオプションも付ける

### P3-G: memo.txt の後片付け

- [x] `memo.txt` を `.gitignore` に追加するか削除する（作業中の手書きメモがリポジトリに残っている）
