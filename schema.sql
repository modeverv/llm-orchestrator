PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    prompt_path TEXT NOT NULL,
    cwd TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('read', 'write', 'deploy')),
    worker TEXT NOT NULL DEFAULT 'gemini',
    status TEXT NOT NULL CHECK (
        status IN (
            'queued',
            'running',
            'succeeded',
            'failed',
            'waiting_human'
        )
    ),
    safe_score REAL NOT NULL,
    c_score REAL NOT NULL,
    o_score REAL NOT NULL,
    i_score REAL NOT NULL,
    ownership_paths TEXT NOT NULL DEFAULT '[]',
    attempts INTEGER NOT NULL DEFAULT 0,
    prompt_template_id INTEGER,
    gemini_session_id TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    cwd TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('read', 'write', 'deploy')),
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    owner TEXT NOT NULL,
    acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project, cwd, job_id)
);

CREATE TABLE IF NOT EXISTS human_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    reason TEXT NOT NULL,
    answer TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'answered')) DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS prompt_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'deprecated')),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT,
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS job_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    prompt_template_id INTEGER,
    worker TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_seconds REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER,
    tokens_out INTEGER,
    step_count INTEGER NOT NULL DEFAULT 0,
    out_of_scope_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_locks_project_cwd ON locks(project, cwd);
CREATE INDEX IF NOT EXISTS idx_human_requests_status ON human_requests(status);
