"""fluxiaRSS 采集器：抓取 RSS → 去重 → 关键词相关过滤 → 概述。"""
from __future__ import annotations

import hashlib

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


def collect_all() -> list[dict]:
    """返回本轮新增文章（尚未入库）。单源超上限的截断。"""
    new: list[dict] = []
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
                    new.append(
                        {
                            "id": aid,
                            "url": link,
                            "title": title,
                            "source": feed["name"],
                            "summary": summarize(title, desc),
                        }
                    )
                    got += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[collector] feed {feed['name']} failed: {exc}")
    return new
