"""fluxiaRSS SQLite 数据层。"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import config
from .classify import classify


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id       TEXT PRIMARY KEY,
                url      TEXT NOT NULL UNIQUE,
                name     TEXT NOT NULL,
                topic    TEXT NOT NULL DEFAULT 'custom',
                custom   INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL
            )
            """
        )
        # 幂等迁移：老库 articles 表没有 category 列，补上
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
        if "category" not in cols:
            conn.execute("ALTER TABLE articles ADD COLUMN category TEXT")
        # 首次初始化时用 config.FEEDS 播种内置源；非空表不重复播种（用户删除的
        # 内置源不会因重启复活）
        row = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()
        if row and row["n"] == 0:
            now = datetime.now(timezone.utc).isoformat()
            for f in config.FEEDS:
                conn.execute(
                    "INSERT INTO sources (id, url, name, topic, custom, added_at) "
                    "VALUES (?, ?, ?, ?, 0, ?)",
                    (_hash(f["url"]), f["url"], f["name"], f["topic"], now),
                )
    # 内置源与 config.FEEDS 对齐（移除已删源的旧文章）+ 存量文章分类回填
    sync_builtin_sources()
    backfill_categories()


def sync_builtin_sources() -> None:
    """内置源与 config.FEEDS 对齐（幂等）。

    - custom=0 且 URL 仍在配置中 → 用配置更新 topic/name（config 权威）
    - custom=0 且已从配置移除 → 删除该源行及其名下文章（如移除 arXiv 后清掉存量）
    - 不重新插入配置里的新内置源，用户删过的内置源不会因重启复活
    """
    with _conn() as conn:
        feeds = {f["url"]: f for f in config.FEEDS}
        rows = conn.execute(
            "SELECT url, name, custom FROM sources"
        ).fetchall()
        removed_names: list[str] = []
        for row in rows:
            if row["custom"]:
                continue
            feed = feeds.get(row["url"])
            if feed:
                conn.execute(
                    "UPDATE sources SET topic=?, name=? WHERE url=? AND custom=0",
                    (feed["topic"], feed["name"], row["url"]),
                )
            else:
                conn.execute(
                    "DELETE FROM sources WHERE url=? AND custom=0", (row["url"],)
                )
                removed_names.append(row["name"])
        for name in removed_names:
            conn.execute("DELETE FROM articles WHERE source=?", (name,))


def backfill_categories() -> None:
    """为尚未分类的文章按规则回填 category（幂等，覆盖老数据）。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, title, summary, source FROM articles WHERE category IS NULL"
        ).fetchall()
        for r in rows:
            cat = classify(r["title"] or "", r["summary"] or "", r["source"] or "")
            conn.execute(
                "UPDATE articles SET category=? WHERE id=?", (cat, r["id"])
            )


def get_article(aid: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
        return dict(row) if row else None


def insert_article(aid: str, url: str, title: str, source: str, summary: str,
                   category: str | None = None) -> bool:
    """插入新文章；若已存在返回 False。category 为 research/practical/news/other。"""
    if get_article(aid):
        return False
    with _conn() as conn:
        conn.execute(
            "INSERT INTO articles (id, url, title, source, summary, category, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, url, title, source, summary, category,
             datetime.now(timezone.utc).isoformat()),
        )
    return True


def list_articles(limit: int, since: str | None = None) -> list[dict]:
    """取最近采集的文章；since 为 ISO 时间时，只取 fetched_at >= since（新鲜池）。"""
    with _conn() as conn:
        if since is None:
            rows = conn.execute(
                "SELECT * FROM articles ORDER BY fetched_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM articles WHERE fetched_at >= ? "
                "ORDER BY fetched_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def get_latest_ratings(article_ids: list[str]) -> dict[str, dict]:
    """返回每篇文章「最近一次评分」+「最近一条非空评论」。

    - score/action 取最近一次真正的动作（read/later/skip）；纯评论行（action=
      comment、score 为空）不作为主状态，避免插件渲染成「已评 null/10」。
    - comment 单独取该文章最近一条非空评论（评论只增不改，与最新动作可能不同行，
      合并返回让跨端能看到用户写的批注）。
    - 无评分的文章不在结果中。
    """
    if not article_ids:
        return {}
    q = ",".join("?" * len(article_ids))
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT r.article_id, r.score, r.action,
                   (SELECT c.comment FROM ratings c
                    WHERE c.article_id = r.article_id AND c.comment IS NOT NULL
                    ORDER BY c.time DESC LIMIT 1) AS comment
            FROM ratings r
            WHERE r.article_id IN ({q})
              AND r.action IN ('read', 'later', 'skip')
              AND r.time = (
                  SELECT MAX(t.time) FROM ratings t
                  WHERE t.article_id = r.article_id AND t.action IN ('read', 'later', 'skip')
              )
            """,
            article_ids,
        ).fetchall()
    return {
        r["article_id"]: {
            "score": r["score"],
            "action": r["action"] or "read",
            "comment": r["comment"],
        }
        for r in rows
    }


def _hash(url: str) -> str:
    """与 collector 一致的 URL 指纹，用作 sources 主键。"""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def list_sources() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sources ORDER BY added_at, name"
        ).fetchall()
        return [dict(r) for r in rows]


def add_source(url: str, name: str | None = None,
               topic: str | None = None) -> dict | None:
    """新增/更新一个 RSS 源（按 URL 幂等 upsert，标记为自定义）。URL 非法返回 None。"""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    try:
        host = urlparse(url).netloc or url
    except ValueError:
        host = url
    name = (name or "").strip() or host
    topic = (topic or "").strip() or "custom"
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sources (id, url, name, topic, custom, added_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(url) DO UPDATE SET name=excluded.name,
                                           topic=excluded.topic,
                                           custom=1
            """,
            (_hash(url), url, name, topic,
             datetime.now(timezone.utc).isoformat()),
        )
        row = conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
        return dict(row)


def remove_source(url: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM sources WHERE url=?", (url,))
        return cur.rowcount > 0


def add_rating(article_id: str, score: int | None, comment: str | None,
               action: str) -> bool:
    """记录一条评分/评论；文章不存在返回 False。

    action == "comment" 且 score 为空时视为「给最近一条评分补评论」：
    更新该文章最近一条尚未带评论的评分行；若无行可更新，退化为插入一条
    纯评论（score 为 NULL，不影响任何均分计算）。
    """
    if not get_article(article_id):
        return False
    if comment and score is None and action == "comment":
        with _conn() as conn:
            cur = conn.execute(
                "UPDATE ratings SET comment=? WHERE article_id=? AND comment IS NULL "
                "ORDER BY time DESC LIMIT 1",
                (comment, article_id),
            )
            if cur.rowcount:
                return True
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


def source_stats() -> dict[str, tuple[float, int]]:
    """每个来源的 (平均分, 评分条数)，供采集期来源门控用。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT a.source AS src, AVG(r.score) AS s, COUNT(*) AS n
            FROM ratings r JOIN articles a ON a.id = r.article_id
            WHERE r.score IS NOT NULL
            GROUP BY a.source
            """
        ).fetchall()
    return {r["src"]: (float(r["s"]), r["n"]) for r in rows if r["n"]}


# 偏好词提取的英文停用词（标题里出现也说明不了偏好）
_STOPWORDS = frozenset(
    """
    the a an of to in on for with and or not is are was were this that
    these those it its be been by from at as into over up out new more
    your you we they he she will can has have how why what when where
    report analysis look takes next things making going could would make
    """.split()
)


def preference_tokens(min_pos: int = 7, max_neg: int = 3) -> tuple[set[str], set[str]]:
    """从已评分文章标题提取正/反偏好词（采集筛选的内容信号）。

    高分(>=min_pos)标题的词 → 正偏好；低分(<=max_neg)标题的词 → 反偏好。
    同时在高低分都出现的词相互抵消（如 "agent" 这种中性词），避免两边误命中。
    无评分时返回两个空集（冷启动不做内容筛选）。
    """
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT a.title AS t, r.score AS s
            FROM ratings r JOIN articles a ON a.id = r.article_id
            WHERE r.score IS NOT NULL
            """
        ).fetchall()
    pos: set[str] = set()
    neg: set[str] = set()
    for r in rows:
        toks = {
            w
            for w in re.split(r"[^a-z0-9]+", (r["t"] or "").lower())
            if len(w) > 2 and w not in _STOPWORDS
        }
        if r["s"] >= min_pos:
            pos |= toks
        elif r["s"] <= max_neg:
            neg |= toks
    # 基础关键词（如 "agent"）在采集里必然出现，从正/反偏好中剔除，
    # 否则低分文章标题里的关键词会让几乎所有候选命中反偏好而被误删。
    kw_tokens = {
        w
        for kw in config.KEYWORDS
        for w in re.split(r"[^a-z0-9]+", kw.lower())
        if len(w) > 2
    }
    pos -= kw_tokens
    neg -= kw_tokens
    pos -= neg
    neg -= pos
    return pos, neg
