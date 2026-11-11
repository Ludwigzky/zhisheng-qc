# -*- coding: utf-8 -*-
"""SQLite 存取层：建表、连接、审计与通知。"""
import json
import os
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "demo.db")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")
REPORT_DIR = os.path.join(DATA_DIR, "reports")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, emp_no TEXT UNIQUE, name TEXT, role TEXT, dept TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS user_credentials(
  user_id INTEGER PRIMARY KEY REFERENCES users(id), password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS auth_sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES user_credentials(user_id),
  expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS auth_sessions_by_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS auth_invites(
  id INTEGER PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, emp_no TEXT NOT NULL, name TEXT NOT NULL,
  dept TEXT NOT NULL, role TEXT NOT NULL, issued_by INTEGER NOT NULL REFERENCES users(id),
  expires_at INTEGER NOT NULL, used_at INTEGER, reset_user_id INTEGER REFERENCES users(id));
CREATE INDEX IF NOT EXISTS auth_invites_by_emp ON auth_invites(emp_no);
CREATE TABLE IF NOT EXISTS auth_limits(
  bucket TEXT PRIMARY KEY, failures INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, model TEXT, customer TEXT,
  contract_no TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY, product_id INTEGER, code TEXT, std_ref TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS form_templates(
  id INTEGER PRIMARY KEY, code TEXT, revision TEXT, title TEXT, std_version TEXT,
  spec_ref TEXT, schema_json TEXT, created_at TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS plan_nodes(
  id INTEGER PRIMARY KEY, plan_id INTEGER, seq INTEGER, name TEXT, short_name TEXT, template_id INTEGER);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, plan_id INTEGER, node_id INTEGER, seq INTEGER, template_id INTEGER,
  template_revision TEXT, status TEXT, assignee_id INTEGER, claimed_at TEXT, done_at TEXT,
  latest_record_id INTEGER);
CREATE TABLE IF NOT EXISTS records(
  id INTEGER PRIMARY KEY, task_id INTEGER, round INTEGER, operator_id INTEGER, created_at TEXT,
  transcript TEXT, engine TEXT, audio_id TEXT, template_id INTEGER, template_revision TEXT,
  std_version TEXT, values_json TEXT, judge_json TEXT, overall TEXT, snapshot TEXT);
CREATE TABLE IF NOT EXISTS signoffs(
  id INTEGER PRIMARY KEY, plan_id INTEGER, role TEXT, user_id INTEGER, name TEXT, at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY, plan_id INTEGER, file TEXT, created_at TEXT, pages INTEGER, snapshot TEXT);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, title TEXT, body TEXT, read INTEGER DEFAULT 0,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, at TEXT, actor_id INTEGER, actor_name TEXT, action TEXT, target TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS asr_samples(
  id INTEGER PRIMARY KEY, at TEXT, user_id INTEGER, script_id TEXT, mode TEXT, denoise INTEGER,
  engine TEXT, expected TEXT, text TEXT, confidence REAL, snr_db REAL, duration REAL,
  cer REAL, ok INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS steel_records(
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
  data_json TEXT NOT NULL, evaluation_json TEXT NOT NULL, template_hash TEXT NOT NULL,
  snapshot TEXT NOT NULL, inspector_name TEXT, signed_at TEXT, parent_id INTEGER);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs():
    for d in (DATA_DIR, AUDIO_DIR, REPORT_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)


def connect():
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        import workflow
        import steel_standards
        import steel_archive
        import management_cases
        import management_ai
        workflow.init_schema(conn)
        steel_standards.init_schema(conn)
        steel_archive.ensure_schema(conn)
        management_cases.init_schema(conn)
        management_ai.init_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """轻量迁移：老库补列，不需要重建。"""
    cols = set(r[1] for r in conn.execute("PRAGMA table_info(asr_samples)"))
    for name, ddl in (("asr_mode", "TEXT"), ("grammar_ok", "INTEGER"), ("grammar_error", "TEXT")):
        if name not in cols:
            conn.execute("ALTER TABLE asr_samples ADD COLUMN %s %s" % (name, ddl))


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def row(conn, sql, args=()):
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def insert(conn, table, data):
    keys = list(data.keys())
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        table, ",".join(keys), ",".join("?" for _ in keys))
    cur = conn.execute(sql, [data[k] for k in keys])
    return cur.lastrowid


def audit(conn, actor, action, target="", detail=None):
    insert(conn, "audit", {
        "at": now(),
        "actor_id": (actor or {}).get("id"),
        "actor_name": (actor or {}).get("name", "system"),
        "action": action,
        "target": target,
        "detail": json.dumps(detail or {}, ensure_ascii=False),
    })
    conn.commit()


def notify(conn, user_id, kind, title, body=""):
    insert(conn, "notifications", {
        "user_id": user_id, "kind": kind, "title": title, "body": body,
        "read": 0, "created_at": now(),
    })
    conn.commit()
