"""SQLite schema + tiny helper layer."""
from __future__ import annotations

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  workspace TEXT NOT NULL DEFAULT 'My workspace',
  ai_provider TEXT DEFAULT 'auto',
  ai_api_key TEXT DEFAULT '',
  ai_model TEXT DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  slug TEXT UNIQUE NOT NULL,
  color TEXT DEFAULT '#4f46e5',
  avatar_emoji TEXT DEFAULT '',
  greeting TEXT DEFAULT '',
  suggestions TEXT DEFAULT '[]',
  model TEXT DEFAULT '',
  temperature REAL DEFAULT 0.4,
  system_prompt TEXT DEFAULT '',
  ask_email INTEGER DEFAULT 1,
  active INTEGER DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  status TEXT DEFAULT 'ready',
  url TEXT DEFAULT '',
  file_path TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  content TEXT DEFAULT '',
  error TEXT DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  bot_id INTEGER NOT NULL,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  visitor_key TEXT DEFAULT '',
  name TEXT DEFAULT '',
  email TEXT DEFAULT '',
  channel TEXT DEFAULT 'Playground',
  status TEXT DEFAULT 'open',
  title TEXT DEFAULT '',
  satisfaction INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  rating INTEGER DEFAULT 0,
  citations TEXT DEFAULT '[]',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  name TEXT DEFAULT '',
  email TEXT DEFAULT '',
  company TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  consent INTEGER DEFAULT 1,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  key TEXT UNIQUE NOT NULL,
  prefix TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  last_used INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS webhooks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  active INTEGER DEFAULT 1,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bots_user ON bots(user_id);
CREATE INDEX IF NOT EXISTS idx_sources_bot ON sources(bot_id);
CREATE INDEX IF NOT EXISTS idx_chunks_bot ON chunks(bot_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_conv_bot ON conversations(bot_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_contacts_bot ON contacts(bot_id);
"""


def now() -> int:
    return int(time.time())


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("suggestions", "citations"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k] or "[]")
            except (ValueError, TypeError):
                d[k] = []
    return d


def rows_to_dicts(rows) -> list[dict]:
    return [row_to_dict(r) for r in rows]
