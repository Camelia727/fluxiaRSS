"""fluxiaRSS 采集器：抓取 RSS → 去重 → 关键词软信号加权 → 并发概述。"""
from __future__ import annotations

import calendar
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser
import httpx

from . import config
from .classify import classify
from .db import (
    get_article,
    get_zone_keywords,
    list_sources,
    preference_tokens,
    source_stats,
    zone_params,
)
from .honcho_client import get_profile
from .llm import summarize
from .relevance import relevant


def _hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


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


def collect_candidates(zone: str = "default") -> list[dict]:
    """采集并过滤相关文章（尚未概述、尚未入库）。

    关键词**软信号**（不硬过滤，只加权）+ 去重之外，评分参与筛选（混合力度）：
    - 来源门控：均分 < SOURCE_MIN_TRUST 且评分 >= SOURCE_MIN_RATINGS 条的源，整轮跳过
    - 内容偏好：反偏好词命中且无正偏好也无关键词命中 → 硬删；命中正偏好/关键词 → 采集权重 +1
    - 探索保底：每源名额内保 EXPLORATION_BUDGET 比例给非偏好文章
    冷启动（无评分）时三档都不生效，行为与原来一致。
    """
    p = zone_params(zone)
    pos, neg = preference_tokens(zone=zone)
    stats = source_stats(zone=zone)
    cands: list[dict] = []
    with httpx.Client(
        follow_redirects=True,
        timeout=20,
        headers={"User-Agent": config.FEED_USER_AGENT},
    ) as client:
        for feed in list_sources(zone=zone):
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
                    # 按源放宽年龄窗口：慢更新源（如 Cloudflare/Vercel）用更长窗口，
                    # 其余源回退全局 MAX_AGE_DAYS
                    # 年龄窗口按区生效：区 config 可给更宽的 max_age_days
                    # （如创作区源更新慢，需要 >2 天窗口），慢更新源仍走按源覆盖
                    src_age = p["source_max_age_days"] or {}
                    max_age = src_age.get(feed["name"], p["max_age_days"])
                    if age is not None and age > max_age:
                        continue
                    aid = _hash(link)
                    if get_article(aid):
                        continue
                    # 关键词软信号（不硬过滤）：命中正偏好/关键词 → 采集加权；
                    # 反偏好命中且无正偏好也无关键词命中才硬删（低分流到
                    # ranking 的 avg_rating 压排名，不在采集期直接 DROP）
                    hay = f"{title} {desc}".lower()
                    hit_neg = any(t in hay for t in neg)
                    hit_pos = any(t in hay for t in pos)
                    kw_hit = relevant(title, desc, keywords=get_zone_keywords(zone))
                    if hit_neg and not (hit_pos or kw_hit):
                        continue
                    feed_cands.append(
                        {
                            "id": aid,
                            "url": link,
                            "title": title,
                            "source": feed["name"],
                            "desc": desc,
                            "_pref": "pos" if (hit_pos or kw_hit) else "neutral",
                        }
                    )
                cands.extend(_pick_by_preference(feed_cands, cap=p["per_feed_cap"]))
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


def _summarize_parallel(cands: list[dict], workers: int,
                        profile: str = "", zone: str = "default",
                        topic: str = "") -> list[dict]:
    def work(item: dict) -> dict:
        item["summary"] = summarize(item["title"], item["desc"], profile, topic=topic)
        # 简单规则分类（research/practical/news/other），随文章入库
        item["category"] = classify(item["title"], item["desc"], item["source"])
        return item

    done: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, c) for c in cands]
        for fut in as_completed(futures):
            done.append(fut.result())
    return done


def collect_all(zone: str = "default", workers: int | None = None) -> list[dict]:
    """返回某区本轮新增文章（含概述）。"""
    workers = workers or config.SUMMARY_WORKERS
    p = zone_params(zone)
    profile = get_profile(zone=zone)
    return _summarize_parallel(
        collect_candidates(zone=zone), workers, profile,
        zone=zone, topic=p["summarize_topic"],
    )
