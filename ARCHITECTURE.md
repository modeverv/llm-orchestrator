# ARCHITECTURE.md — FYWS

## 思想的背景

### LLMは探索機である

LLMに自律的な制御を委ねるアプローチ（Hermes、LangGraphなど）は、確率論的なシステムに決定論的な判断を期待している。これは設計上の誤りだ。

FYWSの役割分担：

```
決定論（Python + SQLite）が担う
  - 次のジョブを何にするか
  - lockを取るか取らないか
  - リトライするかhuman_gateにするか
  - promptのどのversionを使うか
  - jobを成功とみなすか失敗とみなすか

確率論（LLM worker）が担う
  - コードを書く
  - 調査して要約する
  - promptの改善案を出す（approveは人間）
  - human_gateの質問文を作る
```

### safe(T) = C × O × (1 - I)

本著（LLM book-1）で定義したタスク安全性関数をjobルーティングに使う。

- **C**（終了条件の形式化可能度）→ acceptance.mdに書けるか
- **O**（副作用の観測可能度）→ ownership_pathsで列挙できるか  
- **I**（暗黙知依存度）→ context.mdで補えるか

safe値が低いほどhuman_gateを早く発動する。

### ステップ誤差の蓄積 (1-ε)^n

10ステップを超えるjobはチェックポイントを設ける設計にする。
jobをそもそも小さく分割してキューに積むのが基本方針。

---

## レイヤー構成

```
┌─────────────────────────────────────┐
│  cli.py                             │  ← 人間のエントリポイント
│  discord_bot.py                     │  ← Discord gateway
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  orchestrator.py                    │  ← ジョブキュー・ルーティング・決定論的制御
│    - queue_job()                    │
│    - dispatch_next()                │
│    - route_worker()                 │
└──┬───────────┬───────────┬──────────┘
   │           │           │
┌──▼──┐  ┌────▼───┐  ┌────▼──────┐
│lock │  │gate.py │  │evaluator  │
│.py  │  │human   │  │.py        │
│     │  │_gate   │  │メトリクス  │
└──┬──┘  └────────┘  └────┬──────┘
   │                      │
┌──▼──────────────────────▼──────────┐
│  workers/                          │
│    WorkerBase                      │
│      ├── GeminiWorker              │  ← デフォルト（毎日トークンリセット）
│      ├── ClaudeWorker              │  ← 差し替え用
│      └── CodexWorker               │  ← Codex CLI差し替え用
└──────────────┬─────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  runner.py                          │  ← queued job dispatch loop
│  gateway.py                         │  ← external message → job bundle
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  summarizer.py                      │  ← events.jsonl → summary.md
│  （ローカルLLM or 軽量モデル可）       │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  jobs.sqlite3                       │  ← 状態の本体
│  artifacts/<job-id>/                │  ← 成果物の本体
└─────────────────────────────────────┘
```

---

## ジョブのライフサイクル

```
human: cli.py job add --project spobook --prompt task.md --mode write

         queued
           │
           │ dispatch_next()
           │ safe(T) チェック
           │ lock 取得
           ▼
         running
           │
           │ worker.run()
           │ events.jsonl 保存
           │ last_message.txt 保存
           ▼
    git diff --name-only
    所有範囲外の変更？
      YES → waiting_human
      NO  ↓
    2回目のfail?
      YES → waiting_human
      NO  ↓
    summarizer → summary.md
    evaluator  → job_metrics
           │
     ┌─────┴──────┐
     ▼            ▼
  succeeded     failed
                  │
              retry or
            waiting_human
```

---

## セッション継続戦略

Gemini CLIは `--resume latest` でセッションを継続できる。
FYWSは以下の方針でこれを使う：

```
同一Gemini jobの継続 → gemini_session_idが記録済み、かつattempts>0なら --resume latest
別jobへの引き継ぎ → 新セッション + context.md（summary.md + AGENTS.md + task.md）
トークン上限到達  → 現セッション終了 → summarize → 新セッションで再開
```

セッションIDは `--list-sessions` で確認できるが、FYWSは基本的に `latest` で管理する。
Claude/Codex workerではCLI側のresumeに依存せず、jobごとのcontext.mdを正とする。
別jobに `--resume latest` を流用すると前jobの暗黙状態が混ざるため禁止する。

---

## prompt改善ループ

```
job完了
  └→ job_metrics に記録（tokens / duration / outcome / out_of_scope）

一定サンプル蓄積（同一prompt_template で N件以上）
  └→ evaluator.py が分析
       └→ LLMに改善案を生成させる（status='draft'で保存）
            └→ 人間がapprove（status='active'）
                 └→ 次jobから新versionを使用
```

**approveなしで自動activeにしない**。これがルールベース制御を守る最後の砦。

---

## artifacts ディレクトリ構造

```
artifacts/
└── <job-id>/           例: artifacts/42/
    ├── prompt.md       workerに渡したprompt
    ├── context.md      前jobから引き継いだcontext
    ├── events.jsonl    stream-json出力をそのまま保存
    ├── last_message.txt workerの最終出力
    ├── summary.md      固定スキーマのsummary（summarizer生成）
    ├── diff.patch      git diff の出力
    └── acceptance.md   検証条件（job作成時に人間が書く）
```

---

## SQLite設計の要点

- WALモード必須（並列jobの同時書き込み対応）
- foreign_keys=ON（job削除時のevent孤立を防ぐ）
- `locks`テーブルはjob終了時に必ず削除する（クリーンアップ）
- `job_metrics`は`jobs`とは別テーブル（jobが消えてもメトリクスは残す設計も検討）

---

## workerインターフェース規約

```python
@dataclass
class WorkerResult:
    success: bool
    last_message: str
    events_path: str        # events.jsonl のパス
    tokens_in: int | None
    tokens_out: int | None
    step_count: int
    out_of_scope_files: list[str]  # 所有範囲外の変更ファイル
    error: str | None

class WorkerBase:
    def run(
        self,
        prompt_path: str,
        cwd: str,
        artifact_dir: str,
        ownership_paths: list[str],
        resume: bool = False,
        timeout_seconds: float | None = None,
    ) -> WorkerResult: ...
```

GeminiWorkerもClaudeWorkerもCodexWorkerもこのインターフェースを守る。
orchestratorはWorkerBaseしか知らない。

---

## 差し替えのトリガー

以下の条件でworkerをGemini→Claudeに切り替える（手動または自動）：

```
- Geminiのトークンが当日分を使い切った
- Geminiが2回連続でfailしたタスク（Claude側に投げる）
- safe(T) < 0.3 の高リスクタスク（Claudeを使いたい場合）
- deployモードのjob（必ずClaudeまたは人間）
```

切り替えはjobsテーブルの`worker`カラムを変えるだけ。コードは変わらない。
