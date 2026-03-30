"""Database schema initialization for Vidya."""

import sqlite3
from pathlib import Path


_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_items (
    id TEXT PRIMARY KEY,

    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,

    pattern TEXT NOT NULL,
    guidance TEXT NOT NULL,
    type TEXT NOT NULL,
    details_json TEXT,
    tags TEXT DEFAULT '[]',

    base_confidence REAL DEFAULT 0.0,

    source TEXT DEFAULT 'observation',
    evidence TEXT DEFAULT '[]',
    counter_evidence TEXT DEFAULT '[]',

    first_seen TEXT NOT NULL,
    last_fired TEXT,
    fire_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,

    overrides TEXT,
    superseded_by TEXT,
    related_items TEXT DEFAULT '[]',
    version INTEGER DEFAULT 1,

    explanation TEXT,
    status TEXT DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_scope
    ON knowledge_items(language, runtime, framework, project);
CREATE INDEX IF NOT EXISTS idx_status_confidence
    ON knowledge_items(status, base_confidence);
CREATE INDEX IF NOT EXISTS idx_type
    ON knowledge_items(type);
CREATE INDEX IF NOT EXISTS idx_last_fired
    ON knowledge_items(last_fired);

-- Standalone FTS index (item_id maps back to knowledge_items.id)
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    item_id UNINDEXED,
    pattern,
    guidance,
    explanation
);

-- FTS sync triggers — keep knowledge_fts in step with knowledge_items
CREATE TRIGGER IF NOT EXISTS knowledge_items_ai
AFTER INSERT ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(item_id, pattern, guidance, explanation)
    VALUES (new.id, new.pattern, new.guidance, new.explanation);
END;

CREATE TRIGGER IF NOT EXISTS knowledge_items_ad
AFTER DELETE ON knowledge_items BEGIN
    DELETE FROM knowledge_fts WHERE item_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS knowledge_items_au
AFTER UPDATE OF pattern, guidance, explanation ON knowledge_items BEGIN
    DELETE FROM knowledge_fts WHERE item_id = old.id;
    INSERT INTO knowledge_fts(item_id, pattern, guidance, explanation)
    VALUES (new.id, new.pattern, new.guidance, new.explanation);
END;

CREATE TABLE IF NOT EXISTS task_records (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    timestamp_start TEXT NOT NULL,
    timestamp_end TEXT,

    goal TEXT NOT NULL,
    goal_type TEXT,

    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,

    outcome TEXT,
    outcome_detail TEXT,
    failure_type TEXT,

    total_steps INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost REAL DEFAULT 0.0,
    wall_clock_ms INTEGER DEFAULT 0,

    files_touched TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_task_scope
    ON task_records(language, project);
CREATE INDEX IF NOT EXISTS idx_task_outcome
    ON task_records(outcome);

CREATE TABLE IF NOT EXISTS step_records (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task_records(id),
    sequence INTEGER NOT NULL,
    timestamp TEXT NOT NULL,

    thought TEXT,
    action_type TEXT NOT NULL,
    action_name TEXT NOT NULL,
    action_args TEXT,

    result_status TEXT NOT NULL,
    result_output TEXT,
    result_error TEXT,

    alternatives TEXT,
    preconditions TEXT,
    postconditions TEXT,

    duration_ms INTEGER DEFAULT 0,
    tokens_used INTEGER DEFAULT 0,

    UNIQUE(task_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_step_task
    ON step_records(task_id, sequence);

CREATE TABLE IF NOT EXISTS feedback_records (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES task_records(id),
    step_id TEXT REFERENCES step_records(id),
    timestamp TEXT NOT NULL,

    feedback_type TEXT NOT NULL,
    detail TEXT NOT NULL,

    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,

    items_affected TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_feedback_task
    ON feedback_records(task_id);
CREATE INDEX IF NOT EXISTS idx_feedback_type
    ON feedback_records(feedback_type);

CREATE TABLE IF NOT EXISTS extraction_candidates (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,

    pattern TEXT NOT NULL,
    guidance TEXT NOT NULL,
    type TEXT NOT NULL,

    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,

    extraction_method TEXT NOT NULL,
    evidence TEXT NOT NULL,
    initial_confidence REAL DEFAULT 0.0,

    status TEXT DEFAULT 'pending',
    merged_into TEXT,
    review_notes TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_archive (
    id TEXT PRIMARY KEY,
    archived_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    original_data TEXT NOT NULL
);
"""


def init_db(path: str) -> sqlite3.Connection:
    """Create and initialize the Vidya database. Returns an open connection."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    conn.commit()
    return conn
