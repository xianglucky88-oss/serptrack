"""单一职责：本地 sqlite3 持久化与 schema 引导。

通用 records 表保留；SERP 域表按代次/上下文/快照/排名历史组织，
所有键稳定、写入幂等，支持进程重启后只接续未确认工作。
"""
import json
import sqlite3
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS records(
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_versions(
    config_version TEXT PRIMARY KEY,
    matrix_id TEXT NOT NULL,
    matrix_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generations(
    gen_id TEXT PRIMARY KEY,
    config_version TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs(
    job_id TEXT PRIMARY KEY,
    gen_id TEXT NOT NULL,
    config_version TEXT NOT NULL,
    keyword TEXT NOT NULL,
    req_geo TEXT NOT NULL, req_lang TEXT NOT NULL, req_device TEXT NOT NULL,
    act_geo TEXT NOT NULL, act_lang TEXT NOT NULL, act_device TEXT NOT NULL,
    fallback_applied INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT NOT NULL DEFAULT '',
    batch_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    snapshot_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks(
    task_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    gen_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    site_id TEXT NOT NULL,
    host TEXT NOT NULL,
    req_geo TEXT NOT NULL, req_lang TEXT NOT NULL, req_device TEXT NOT NULL,
    act_geo TEXT NOT NULL, act_lang TEXT NOT NULL, act_device TEXT NOT NULL,
    fallback_applied INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    lease_owner TEXT, lease_expires_at TEXT,
    snapshot_id TEXT,
    rank_state TEXT NOT NULL DEFAULT 'not_collected',
    organic_positions TEXT NOT NULL DEFAULT '[]',
    ad_positions TEXT NOT NULL DEFAULT '[]',
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pages(
    page_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    gen_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    geo TEXT NOT NULL, lang TEXT NOT NULL, device TEXT NOT NULL,
    page_no INTEGER NOT NULL,
    complete INTEGER NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0,
    async_pending TEXT NOT NULL DEFAULT '[]',
    raw_blocks TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS snapshots(
    snapshot_id TEXT PRIMARY KEY,
    gen_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    geo TEXT NOT NULL, lang TEXT NOT NULL, device TEXT NOT NULL,
    req_geo TEXT NOT NULL, req_lang TEXT NOT NULL, req_device TEXT NOT NULL,
    fallback_applied INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT NOT NULL DEFAULT '',
    is_complete INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    batch_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS parsed_entries(
    entry_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    gen_id TEXT NOT NULL,
    block_type TEXT NOT NULL,
    host TEXT NOT NULL,
    url TEXT,
    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    page_no INTEGER NOT NULL,
    position INTEGER NOT NULL,
    ordinal INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rank_history(
    rank_id TEXT PRIMARY KEY,
    gen_id TEXT NOT NULL,
    config_version TEXT NOT NULL,
    context_key TEXT NOT NULL,
    keyword TEXT NOT NULL,
    site_id TEXT NOT NULL,
    host TEXT NOT NULL,
    act_geo TEXT NOT NULL, act_lang TEXT NOT NULL, act_device TEXT NOT NULL,
    req_geo TEXT NOT NULL, req_lang TEXT NOT NULL, req_device TEXT NOT NULL,
    fallback_applied INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT NOT NULL DEFAULT '',
    rank_state TEXT NOT NULL,
    organic_positions TEXT NOT NULL DEFAULT '[]',
    ad_positions TEXT NOT NULL DEFAULT '[]',
    occurrences_json TEXT NOT NULL DEFAULT '[]',
    is_complete INTEGER NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    snapshot_id TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(gen_id, context_key)
);

CREATE TABLE IF NOT EXISTS rejected_deliveries(
    reject_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    gen_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs(
    run_id TEXT PRIMARY KEY,
    config_version TEXT NOT NULL,
    budget_total INTEGER NOT NULL,
    budget_used INTEGER NOT NULL DEFAULT 0,
    max_concurrency INTEGER NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_rank_ctx ON rank_history(context_key, gen_id);
CREATE INDEX IF NOT EXISTS idx_tasks_claim ON tasks(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_jobs_gen ON jobs(gen_id, status);
CREATE INDEX IF NOT EXISTS idx_entries_snap ON parsed_entries(snapshot_id);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    bootstrap(con)
    return con


def bootstrap(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


# --- 通用 records（保留原有合法输入链路）---
def upsert(con, record):
    con.execute(
        "INSERT INTO records(key,data) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET data=excluded.data",
        (record.key, json.dumps(record.data, ensure_ascii=False)),
    )


def commit(con):
    con.commit()


def count(con):
    return con.execute("SELECT COUNT(*) FROM records").fetchone()[0]


def all_records(con):
    return [(k, json.loads(d)) for k, d in con.execute("SELECT key,data FROM records")]
