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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                article_id TEXT NOT NULL,
                score      INTEGER,
                comment    TEXT,
                action     TEXT,
                time       TEXT NOT NULL,
                FOREIGN KEY (article_id) REFERENCES articles(id)
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


def add_rating(article_id: str, score: int | None, comment: str | None,
               action: str) -> bool:
    """记录一条评分/评论；文章不存在返回 False。"""
    if not get_article(article_id):
        return False
    with _conn() as conn:
        conn.execute(
            "INSERT INTO ratings (article_id, score, comment, action, time) "
            "VALUES (?, ?, ?, ?, ?)",
            (article_id, score, comment, action,
             datetime.now(timezone.utc).isoformat()),
        )
    return True


def get_article_avg_rating(article_id: str) -> float:
    """该文章的平均评分；无评分返回 0.0。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT AVG(score) AS s, COUNT(*) AS n FROM ratings "
            "WHERE article_id=? AND score IS NOT NULL",
            (article_id,),
        ).fetchone()
    if row and row["n"]:
        return float(row["s"])
    return 0.0


def source_trust() -> dict[str, float]:
    """每个来源的平均评分（用于排序的来源信任分）。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT a.source AS src, AVG(r.score) AS s, COUNT(*) AS n
            FROM ratings r JOIN articles a ON a.id = r.article_id
            WHERE r.score IS NOT NULL
            GROUP BY a.source
            """
        ).fetchall()
    return {r["src"]: float(r["s"]) for r in rows if r["n"]}
