"""fluxiaRSS 采集器：抓取 RSS → 去重 → 关键词相关过滤 → 并发概述。"""
from __future__ import annotations

import hashlib
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser
import httpx

from . import config
from .db import get_article, list_sources, preference_tokens, source_stats
from .llm import summarize


def _hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _relevant(title: str, desc: str) -> bool:
    text = f"{title} {desc}".lower()
    return any(kw in text for kw in config.KEYWORDS)


def _entry_age_days(entry) -> float | None:
    """条目发布距今的天数；无时间字段返回 None（放行，不误杀无日期条目）。"""
    ts = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if ts is None:
        return None
    try:
        return (datetime.now(timezone.utc).timestamp() - calendar.timegm(ts)) / 86400.0
    except (TypeError, ValueError, OverflowError):
        return None


def collect_candidates() -> list[dict]:
    """采集并过滤相关文章（尚未概述、尚未入库）。

    关键词过滤 + 去重之外，评分参与筛选（混合力度）：
    - 来源门控：均分 < SOURCE_MIN_TRUST 且评分 >= SOURCE_MIN_RATINGS 条的源，整轮跳过
    - 内容偏好：反偏好词命中且无正偏好 → 硬删；命中正偏好 → 采集权重 +1
    - 探索保底：每源名额内保 EXPLORATION_BUDGET 比例给非偏好文章
    冷启动（无评分）时三档都不生效，行为与原来一致。
    """
    pos, neg = preference_tokens()
    stats = source_stats()
    cands: list[dict] = []
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        for feed in list_sources():
            # 来源门控（硬）：低分源整轮跳过
            trust, n = stats.get(feed["name"], (None, 0))
            if (
                trust is not None
                and n >= config.SOURCE_MIN_RATINGS
                and trust < config.SOURCE_MIN_TRUST
            ):
                print(
                    f"[collector] skip source {feed['name']}: "
                    f"trust={trust:.1f} n={n}"
                )
                continue
            try:
                text = client.get(feed["url"]).text
                parsed = feedparser.parse(text)
                feed_cands: list[dict] = []
                for entry in parsed.entries:
                    if len(feed_cands) >= config.PER_FEED_SCAN_CAP:
                        break
                    title = getattr(entry, "title", "") or ""
                    desc = (
                        getattr(entry, "summary", "")
                        or getattr(entry, "description", "")
                        or ""
                    )
                    link = getattr(entry, "link", "") or ""
                    if not link or not title:
                        continue
                    age = _entry_age_days(entry)
                    if age is not None and age > config.MAX_AGE_DAYS:
                        continue
                    if not _relevant(title, desc):
                        continue
                    aid = _hash(link)
                    if get_article(aid):
                        continue
                    # 内容偏好：反偏好词硬删；命中正偏好则标记加权
                    hay = f"{title} {desc}".lower()
                    hit_neg = any(t in hay for t in neg)
                    hit_pos = any(t in hay for t in pos)
                    if hit_neg and not hit_pos:
                        continue
                    feed_cands.append(
                        {
                            "id": aid,
                            "url": link,
                            "title": title,
                            "source": feed["name"],
                            "desc": desc,
                            "_pref": "pos" if hit_pos else "neutral",
                        }
                    )
                cands.extend(_pick_by_preference(feed_cands))
            except Exception as exc:  # noqa: BLE001
                print(f"[collector] feed {feed['name']} failed: {exc}")
    return cands


def _pick_by_preference(feed_cands: list[dict],
                        cap: int | None = None) -> list[dict]:
    """每源名额内：正偏好优先填位，探索保底给非偏好文章。

    探索名额 = cap * EXPLORATION_BUDGET（向下取整），保证至少这些名额
    留给非偏好文章；正偏好不足时自然由非偏好补位。
    """
    cap = cap or config.PER_FEED_CAP
    explore = max(0, int(cap * config.EXPLORATION_BUDGET))
    if explore >= cap:
        keep = feed_cands[:cap]
    else:
        pos_cands = [c for c in feed_cands if c["_pref"] == "pos"]
        neutral = [c for c in feed_cands if c["_pref"] == "neutral"]
        n_pos = min(len(pos_cands), cap - explore)
        keep = pos_cands[:n_pos] + neutral[: cap - n_pos]
    for c in keep:
        c.pop("_pref", None)
    return keep


def _summarize_parallel(cands: list[dict], workers: int) -> list[dict]:
    def work(item: dict) -> dict:
        item["summary"] = summarize(item["title"], item["desc"])
        return item

    done: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, c) for c in cands]
        for fut in as_completed(futures):
            done.append(fut.result())
    return done


def collect_all(workers: int | None = None) -> list[dict]:
    """返回本轮新增文章（含概述）。并发调 DeepSeek。"""
    workers = workers or config.SUMMARY_WORKERS
    return _summarize_parallel(collect_candidates(), workers)
