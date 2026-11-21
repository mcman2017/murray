"""SQLite storage: one file, WAL, one connection.

The app is a single process with one event loop. Queries are short, so they run synchronously on the loop; an RLock
guards the connection for the few places that use a worker thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, kind TEXT, status TEXT, cursor_json TEXT, config_json TEXT, budget_usd REAL,
  started_at TEXT, finished_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, kind TEXT, ip_hash TEXT, turn_count INTEGER DEFAULT 0, render_count INTEGER DEFAULT 0,
  broca_interaction_id TEXT, broca_depth INTEGER DEFAULT 0, created_at TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS turns(
  id INTEGER PRIMARY KEY, session_id TEXT, scope TEXT, system TEXT, status TEXT, user_text TEXT, upload_id INTEGER,
  reply_text TEXT, error TEXT, latency_ms INTEGER, cost_usd REAL, meta_json TEXT, created_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id, id);
CREATE TABLE IF NOT EXISTS episodes(
  id INTEGER PRIMARY KEY, scope TEXT, run_id INTEGER, day INTEGER, tick INTEGER, turn_id INTEGER, kind TEXT,
  source TEXT, topic TEXT, stimulus TEXT, response TEXT, salience INTEGER, importance INTEGER, spwr INTEGER DEFAULT 0,
  ignited INTEGER DEFAULT 0, gate_json TEXT, neon_json TEXT, workspace_json TEXT, argon_json TEXT,
  render_job_id INTEGER, render_id INTEGER, image_path TEXT, injection_flag INTEGER DEFAULT 0, created_at TEXT);
CREATE INDEX IF NOT EXISTS episodes_scope ON episodes(scope, id);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(stimulus, response, topic, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS workspace(
  scope TEXT PRIMARY KEY, active_goal TEXT DEFAULT '', current_conflict_or_defect TEXT DEFAULT '',
  plan_step TEXT DEFAULT '', visual_spatial_state TEXT DEFAULT '', conflict_key TEXT DEFAULT '',
  stuck_streak INTEGER DEFAULT 0, tick INTEGER DEFAULT 0, refractory_until INTEGER DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS preferences(
  id INTEGER PRIMARY KEY, scope TEXT, kind TEXT, topic TEXT, statement TEXT, episode_ids_json TEXT,
  evidence_quote TEXT, created_day INTEGER, sleep_id INTEGER, expired_at TEXT, superseded_by INTEGER);
CREATE TABLE IF NOT EXISTS accomplishments(
  id INTEGER PRIMARY KEY, scope TEXT, kind TEXT, claim TEXT, episode_ids_json TEXT, evidence_quote TEXT,
  render_job_id INTEGER, created_day INTEGER, sleep_id INTEGER);
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY, scope TEXT, key TEXT, value TEXT, episode_ids_json TEXT, sleep_id INTEGER,
  created_day INTEGER, expired_at TEXT, superseded_by INTEGER);
CREATE TABLE IF NOT EXISTS sleeps(
  id INTEGER PRIMARY KEY, scope TEXT, day INTEGER, replayed_ids_json TEXT, raw_json TEXT, accepted_json TEXT,
  dropped_json TEXT, trigger TEXT, status TEXT, started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS persona(scope TEXT PRIMARY KEY, self_narrative TEXT, sleep_id INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS persona_snapshots(
  id INTEGER PRIMARY KEY, scope TEXT, day INTEGER, persona_text TEXT, self_narrative TEXT, detail_json TEXT,
  sleep_id INTEGER, created_at TEXT, UNIQUE(scope, day));
CREATE TABLE IF NOT EXISTS render_jobs(
  id INTEGER PRIMARY KEY, scope TEXT, session_id TEXT, origin_episode_id INTEGER, origin_neon_interaction_id TEXT,
  request_text TEXT, fractal_type TEXT, status TEXT, iterations INTEGER DEFAULT 0, final_render_id INTEGER,
  must_disclose_json TEXT, dir TEXT, created_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS renders(
  id INTEGER PRIMARY KEY, job_id INTEGER, iteration INTEGER, approach TEXT, view TEXT, code TEXT,
  sandbox_status TEXT, stderr_tail TEXT, wall_ms INTEGER, image_path TEXT, sheet_path TEXT, gif_path TEXT,
  n_frames INTEGER DEFAULT 1, stats_json TEXT, hard_fail TEXT, argon_json TEXT, argon_verdict TEXT,
  neon_correction TEXT, worker_interaction_id TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS renders_job ON renders(job_id, iteration);
CREATE TABLE IF NOT EXISTS uploads(
  id INTEGER PRIMARY KEY, session_id TEXT, path TEXT, sha256 TEXT, width INTEGER, height INTEGER, stats_json TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, scope TEXT, session_id TEXT, turn_id INTEGER, type TEXT, payload_json TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS events_scope ON events(scope, id);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, id);
CREATE TABLE IF NOT EXISTS llm_calls(
  id INTEGER PRIMARY KEY, ts_start REAL, ts_end REAL, role TEXT, model TEXT, thinking_level TEXT, context TEXT,
  scope TEXT, turn_id INTEGER, run_id INTEGER, interaction_id TEXT, previous_interaction_id TEXT, status TEXT,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, thought_tokens INTEGER DEFAULT 0,
  cached_tokens INTEGER DEFAULT 0, est_cost_usd REAL DEFAULT 0, cost_usd REAL DEFAULT 0, latency_ms INTEGER, error TEXT);
CREATE INDEX IF NOT EXISTS llm_calls_ts ON llm_calls(ts_start);
CREATE TABLE IF NOT EXISTS eval_runs(
  id INTEGER PRIMARY KEY, label TEXT UNIQUE, status TEXT, image_tag TEXT, flags_json TEXT, prompt_hash TEXT,
  test_set_sha256 TEXT, detector_sha256 TEXT, progress_json TEXT, started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS eval_results(
  id INTEGER PRIMARY KEY, eval_run_id INTEGER, case_id TEXT, system TEXT, split TEXT, category TEXT,
  session_id TEXT, transcript_json TEXT, final_render_id INTEGER, artifacts_json TEXT, det_json TEXT,
  judge_json TEXT, score REAL, metrics_json TEXT, latency_ms INTEGER, cost_usd REAL, error TEXT, created_at TEXT,
  UNIQUE(eval_run_id, case_id, system));
CREATE TABLE IF NOT EXISTS eval_scorecards(eval_run_id INTEGER PRIMARY KEY, json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS failure_log(
  id INTEGER PRIMARY KEY, eval_label TEXT, case_id TEXT, title TEXT, observed TEXT, root_cause TEXT, fix TEXT,
  status TEXT, evidence_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""


# Columns added after the first release; applied to databases created before them.
MIGRATIONS = [("render_jobs", "dir", "TEXT"), ("llm_calls", "key_slot", "INTEGER")]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=10000", "foreign_keys=ON"):
            self.conn.execute(f"PRAGMA {pragma}")
        with self.lock:
            self.conn.executescript(SCHEMA)
            for table, column, kind in MIGRATIONS:
                have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, tuple(args)).fetchone()

    def val(self, sql: str, args: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.one(sql, args)
        return default if row is None or row[0] is None else row[0]

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, tuple(args))
            return cur.lastrowid or 0

    def insert(self, table: str, **cols: Any) -> int:
        keys = list(cols.keys())
        sql = f"INSERT INTO {table}({','.join(keys)}) VALUES({','.join('?' for _ in keys)})"
        return self.x(sql, [cols[k] for k in keys])

    def update(self, table: str, row_id: Any, key: str = "id", **cols: Any) -> None:
        if not cols:
            return
        sets = ",".join(f"{k}=?" for k in cols)
        self.x(f"UPDATE {table} SET {sets} WHERE {key}=?", [*cols.values(), row_id])

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        return self.val("SELECT value FROM kv WHERE key=?", (key,), default)

    def kv_set(self, key: str, value: str) -> None:
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def backup_to(self, dest: str | Path) -> None:
        """A consistent copy of the live database (WAL included)."""
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.conn.execute("VACUUM INTO ?", (str(dest),))

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def rowdict(row: sqlite3.Row | None) -> dict:
    return {} if row is None else {k: row[k] for k in row.keys()}
