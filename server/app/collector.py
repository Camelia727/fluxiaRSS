"""fluxiaRSS 采集器：抓取 RSS → 去重 → 关键词相关过滤 → 并发概述。"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import httpx

from . import config
from .db import get_article
from .llm import summarize


def _hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _relevant(title: str, desc: str) -> bool:
    text = f"{title} {desc}".lower()
    return any(kw in text for kw in config.KEYWORDS)


def collect_candidates() -> list[dict]:
    """采集并过滤相关文章（尚未概述、尚未入库）。"""
    cands: list[dict] = []
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        for feed in config.FEEDS:
            got = 0
            try:
                text = client.get(feed["url"]).text
                parsed = feedparser.parse(text)
                for entry in parsed.entries:
                    if got >= config.PER_FEED_CAP:
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
                    if not _relevant(title, desc):
                        continue
                    aid = _hash(link)
                    if get_article(aid):
                        continue
                    cands.append(
                        {
                            "id": aid,
                            "url": link,
                            "title": title,
                            "source": feed["name"],
                            "desc": desc,
                        }
                    )
                    got += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[collector] feed {feed['name']} failed: {exc}")
    return cands


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
