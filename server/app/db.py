"""fluxiaRSS SQLite 数据层。"""
from __future__ import annotations

import hashlib
import json
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
        conn.execute(
            """CREATE TABLE IF NOT EXISTS zones (
                id         TEXT PRIMARY KEY,
                display    TEXT NOT NULL,
                feeds      TEXT NOT NULL DEFAULT '[]',
                keywords   TEXT NOT NULL DEFAULT '[]',
                config     TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deleted_sources (
                url        TEXT PRIMARY KEY,
                deleted_at TEXT NOT NULL
            )
            """
        )
        # 幂等迁移：老库 articles 表没有 category 列，补上
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
        if "category" not in cols:
            conn.execute("ALTER TABLE articles ADD COLUMN category TEXT")
        if "zone" not in cols:
            conn.execute(
                "ALTER TABLE articles ADD COLUMN zone TEXT NOT NULL DEFAULT 'default'"
            )
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
        _seed_zones(conn)
    # 下面两个各自开连接，必须等上面的写事务提交后再跑
    sync_builtin_sources()
    backfill_categories()


def _seed_zones(conn: sqlite3.Connection) -> None:
    """首次启动时播种分区：旧 sources 表 -> default 区，然后播种 ZONES_SEED。

    幂等：zones 表非空时直接返回。旧库（有 sources、无 zones）会把 sources
    整体迁移成 default 区的 feeds，保证升级后旧配置不丢。
    """
    row = conn.execute("SELECT COUNT(*) AS n FROM zones").fetchone()
    if row and row["n"]:
        return
    now = datetime.now(timezone.utc).isoformat()
    src_rows = conn.execute("SELECT name, url, topic FROM sources").fetchall()
    if src_rows:
        feeds_j = json.dumps(
            [{"name": r["name"], "url": r["url"], "topic": r["topic"]} for r in src_rows]
        )
        cfg_j = json.dumps({
            "digest_size": config.DEFAULT_DIGEST_SIZE,
            "fresh_window_hours": config.FRESH_WINDOW_HOURS,
            "max_per_source": config.DIGEST_MAX_PER_SOURCE,
            "research_max": config.RESEARCH_MAX_IN_DIGEST,
            "max_age_days": config.MAX_AGE_DAYS,
            "collect_hour": config.COLLECT_HOUR,
            "collect_minute": config.COLLECT_MINUTE,
            "honcho_workspace": config.HONCHO_WORKSPACE_ID,
            "honcho_session": config.HONCHO_SESSION_ID,
        })
        conn.execute(
            "INSERT INTO zones (id, display, feeds, keywords, config, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("default", "默认区", feeds_j, json.dumps(config.KEYWORDS), cfg_j, now),
        )
        print("[db] migrated sources -> zone default")
    else:
        conn.execute(
            "INSERT INTO zones (id, display, feeds, keywords, config, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("default", "默认区", "[]", "[]", "{}", now),
        )
    for zid, zdata in config.ZONES_SEED.items():
        if conn.execute("SELECT id FROM zones WHERE id=?", (zid,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO zones (id, display, feeds, keywords, config, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (zid, zdata.get("display", zid),
             json.dumps(zdata.get("feeds", []), ensure_ascii=False),
             json.dumps(zdata.get("keywords", []), ensure_ascii=False),
             json.dumps(zdata.get("config", {}), ensure_ascii=False),
             now),
        )
        print(f"[db] seeded zone {zid}")


def sync_builtin_sources() -> None:
    """内置源与 config.FEEDS 对齐（幂等）。

    - custom=0 且 URL 仍在配置中 → 用配置更新 topic/name（config 权威）
    - custom=0 且已从配置移除 → 删除该源行及其名下文章（如移除 arXiv 后清掉存量）
    - 配置里新增、表中缺失的内置源 → 插入为 custom=0；但用户删除过的内置源
      记在 deleted_sources 墓碑表里，不会被复活（区分「新加」与「用户删过」）
    """
    with _conn() as conn:
        feeds = {f["url"]: f for f in config.FEEDS}
        rows = conn.execute(
            "SELECT url, name, custom FROM sources"
        ).fetchall()
        present = {r["url"] for r in rows}
        tombstones = {
            r["url"]
            for r in conn.execute("SELECT url FROM deleted_sources").fetchall()
        }
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
        # 新增内置源：config.FEEDS 里有但表中没有、且未被用户删除过的，补插
        now = datetime.now(timezone.utc).isoformat()
        for url, feed in feeds.items():
            if url in present or url in tombstones:
                continue
            conn.execute(
                "INSERT INTO sources (id, url, name, topic, custom, added_at) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (_hash(url), url, feed["name"], feed["topic"], now),
            )
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
                   category: str | None = None,
                   zone: str = "default") -> bool:
    """插入新文章；若已存在返回 False。"""
    if get_article(aid):
        return False
    with _conn() as conn:
        conn.execute(
            "INSERT INTO articles (id, url, title, source, summary, category, fetched_at, zone) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, url, title, source, summary, category,
             datetime.now(timezone.utc).isoformat(), zone),
        )
    return True

def list_articles(limit: int, since: str | None = None,
                 zone: str = "default") -> list[dict]:
    """取最近采集的文章；since 为 ISO 时间时，只取 fetched_at >= since（新鲜池）。"""
    with _conn() as conn:
        if since is not None:
            rows = conn.execute(
                "SELECT * FROM articles WHERE zone=? AND fetched_at>=? ORDER BY fetched_at DESC LIMIT ?",
                (zone, since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM articles WHERE zone=? ORDER BY fetched_at DESC LIMIT ?",
                (zone, limit),
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


def list_sources(zone: str = "default") -> list[dict]:
    z = get_zone(zone)
    if not z:
        return []
    return z["feeds"]


def add_source(url: str, name: str | None = None,
               topic: str | None = None,
               zone: str = "default") -> dict | None:
    """向某个区添加一个 RSS 源（操作该区的 feeds JSON）。"""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        if not host:
            return None
    except Exception:
        return None
    z = get_zone(zone)
    if not z:
        return None
    feeds = list(z["feeds"])
    for f_item in feeds:
        if f_item["url"] == url:
            return f_item
    new_f = {"name": name or host, "url": url, "topic": topic or "custom"}
    feeds.append(new_f)
    with _conn() as conn:
        conn.execute("UPDATE zones SET feeds=? WHERE id=?", (json.dumps(feeds, ensure_ascii=False), zone))
    return new_f

def remove_source(url: str, zone: str = "default") -> bool:
    """从某个区的 feeds 列表中移除一个 RSS 源。"""
    z = get_zone(zone)
    if not z:
        return False
    feeds = z["feeds"]
    new_feeds = [f for f in feeds if f["url"] != url]
    if len(new_feeds) == len(feeds):
        return False
    with _conn() as conn:
        conn.execute("UPDATE zones SET feeds=? WHERE id=?", (json.dumps(new_feeds, ensure_ascii=False), zone))
    return True

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


def source_trust(zone: str = "default") -> dict[str, float]:
    """每个来源的平均评分（用于排序的来源信任分）。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT a.source AS src, AVG(r.score) AS s, COUNT(*) AS n
            FROM ratings r JOIN articles a ON a.id = r.article_id
            WHERE r.score IS NOT NULL AND a.zone=?
            GROUP BY a.source
            """,
            (zone,),
        ).fetchall()
    return {r["src"]: float(r["s"]) for r in rows if r["n"]}


def source_stats(zone: str = "default") -> dict[str, tuple[float, int]]:
    """每个来源的 (平均分, 评分条数)，供采集期来源门控用。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT a.source AS src, AVG(r.score) AS s, COUNT(*) AS n
            FROM ratings r JOIN articles a ON a.id = r.article_id
            WHERE r.score IS NOT NULL AND a.zone=?
            GROUP BY a.source
            """,
            (zone,),
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


def preference_tokens(min_pos: int = 7, max_neg: int = 3,
                zone: str = "default") -> tuple[set[str], set[str]]:
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
            WHERE r.score IS NOT NULL AND a.zone=?
            """,
            (zone,),
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


# ---- Zone CRUD (自由分区) ----

def get_zone(zone_id: str) -> dict | None:
    """读取一个 zone 的完整配置（含解析后的 feeds/keywords/config）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM zones WHERE id=?", (zone_id,)
        ).fetchone()
    if not row:
        return None
    feeds = json.loads(row["feeds"]) if row["feeds"] else []
    config_data = json.loads(row["config"]) if row["config"] else {}
    return {
        "id": row["id"],
        "display": row["display"],
        "feeds": feeds,
        "keywords": json.loads(row["keywords"]) if row["keywords"] else [],
        "config": config_data,
        "created_at": row["created_at"],
    }


def list_zones() -> list[dict]:
    """列出所有 zone。"""
    with _conn() as conn:
        rows = conn.execute("SELECT id, display, feeds, keywords, config, created_at FROM zones ORDER BY id").fetchall()
    out = []
    for r in rows:
        feeds = json.loads(r["feeds"]) if r["feeds"] else []
        kws = json.loads(r["keywords"]) if r["keywords"] else []
        out.append({
            "id": r["id"],
            "display": r["display"],
            "feed_count": len(feeds),
            "keyword_count": len(kws),
            "created_at": r["created_at"],
        })
    return out


def create_zone(zone_id: str, display: str, feeds: list | None = None,
                keywords: list | None = None, config_data: dict | None = None) -> dict | None:
    """创建一个新区；已存在返回 None。"""
    with _conn() as conn:
        existing = conn.execute("SELECT id FROM zones WHERE id=?", (zone_id,)).fetchone()
        if existing:
            return None
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO zones (id, display, feeds, keywords, config, created_at) VALUES (?,?,?,?,?,?)",
            (zone_id, display,
             json.dumps(feeds or [], ensure_ascii=False),
             json.dumps(keywords or [], ensure_ascii=False),
             json.dumps(config_data or {}, ensure_ascii=False),
             now),
        )
    return get_zone(zone_id)


def update_zone(zone_id: str, display: str | None = None,
                feeds: list | None = None, keywords: list | None = None,
                config_data: dict | None = None) -> dict | None:
    """更新区配置；不存在返回 None。只更新非 None 字段。"""
    z = get_zone(zone_id)
    if not z:
        return None
    new_display = display if display is not None else z["display"]
    new_feeds = feeds if feeds is not None else z["feeds"]
    new_keywords = keywords if keywords is not None else z["keywords"]
    new_config = config_data if config_data is not None else z["config"]
    with _conn() as conn:
        conn.execute(
            "UPDATE zones SET display=?, feeds=?, keywords=?, config=? WHERE id=?",
            (new_display,
             json.dumps(new_feeds, ensure_ascii=False),
             json.dumps(new_keywords, ensure_ascii=False),
             json.dumps(new_config, ensure_ascii=False),
             zone_id),
        )
    return get_zone(zone_id)


def delete_zone(zone_id: str) -> tuple[bool, str]:
    """删除一个区及其名下所有文章。返回 (是否成功, 失败原因)。

    约束：至少保留一个区。删掉最后一个区会让 API 无从下手（所有端点都需要
    区标识），因此最后一个区不允许删；default 不设额外保护，只要有别的区
    就可以删掉它。调用方负责把 404（区不存在）与 409（最后一个区）区分开。
    """
    with _conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM zones").fetchone()["n"]
        exists = conn.execute(
            "SELECT id FROM zones WHERE id=?", (zone_id,)
        ).fetchone()
        if not exists:
            return False, "not_found"
        if count <= 1:
            return False, "last_zone"
        conn.execute("DELETE FROM articles WHERE zone=?", (zone_id,))
        conn.execute("DELETE FROM ratings WHERE article_id NOT IN "
                     "(SELECT id FROM articles)")
        conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
    return True, ""


def get_zone_keywords(zone_id: str) -> list[str]:
    """获取某个区的关键词列表。"""
    z = get_zone(zone_id)
    if not z:
        return []
    return z.get("keywords") or []


# ---- Zone 生效参数（区内配置优先，缺失回退全局 config） ----

def zone_params(zone: str) -> dict:
    """返回某区生效的采集/筛选/Honcho 参数。

    zone.config 中显式提供的键优先；未提供的回退到全局 config 常量，
    保证 default 区与旧行为完全一致。digest_size / fresh_window_hours /
    max_age_days 等都在此统一解析，调用方不再直接读 config。
    """
    z = get_zone(zone)
    cfg = (z or {}).get("config") or {}

    def pick(key, fallback):
        v = cfg.get(key)
        return fallback if v is None else v

    return {
        "digest_size": pick("digest_size", config.DEFAULT_DIGEST_SIZE),
        "fresh_window_hours": pick("fresh_window_hours", config.FRESH_WINDOW_HOURS),
        "max_per_source": pick("max_per_source", config.DIGEST_MAX_PER_SOURCE),
        "research_max": pick("research_max", config.RESEARCH_MAX_IN_DIGEST),
        "per_feed_cap": pick("per_feed_cap", config.PER_FEED_CAP),
        "max_age_days": pick("max_age_days", config.MAX_AGE_DAYS),
        "source_max_age_days": pick("source_max_age_days", config.SOURCE_MAX_AGE_DAYS),
        "summarize_topic": pick("summarize_topic", config.SUMMARIZE_TOPIC),
        "honcho_workspace": pick("honcho_workspace", config.HONCHO_WORKSPACE_ID),
        "honcho_session": pick("honcho_session", config.HONCHO_SESSION_ID),
    }
