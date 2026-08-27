"""fluxiaRSS SQLite 数据层。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config


def _conn() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id         TEXT PRIMARY KEY,
                url        TEXT NOT NULL,
                title      TEXT NOT NULL,
                source     TEXT,
                summary    TEXT,
                fetched_at TEXT NOT NULL
            )
            """
        )


def get_article(aid: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
        return dict(row) if row else None


def insert_article(aid: str, url: str, title: str, source: str, summary: str) -> bool:
    """插入新文章；若已存在返回 False。"""
    if get_article(aid):
        return False
    with _conn() as conn:
        conn.execute(
            "INSERT INTO articles (id, url, title, source, summary, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (aid, url, title, source, summary, datetime.now(timezone.utc).isoformat()),
        )
    return True


def list_articles(limit: int) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM articles ORDER BY fetched_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
