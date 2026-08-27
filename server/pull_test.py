"""在服务器上运行：逐源测试 v1 采集器拉取是否正常。
用法：cd /opt/fluxiars && .venv/bin/python pull_test.py
"""
from __future__ import annotations

import httpx
import feedparser

from app import config
from app.collector import _hash, _relevant
from app.db import get_article, init_db


def main() -> None:
    init_db()
    print(f"{'SOURCE':<22}{'RAW':>6}{'REL':>6}{'NEW':>6}")
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        for feed in config.FEEDS:
            try:
                text = client.get(feed["url"]).text
                parsed = feedparser.parse(text)
                raw = len(parsed.entries)
                rel = new = 0
                for entry in parsed.entries[: config.PER_FEED_CAP * 3]:
                    title = getattr(entry, "title", "") or ""
                    desc = (
                        getattr(entry, "summary", "")
                        or getattr(entry, "description", "")
                        or ""
                    )
                    link = getattr(entry, "link", "") or ""
                    if not _relevant(title, desc):
                        continue
                    rel += 1
                    if link and not get_article(_hash(link)):
                        new += 1
                print(f"{feed['name']:<22}{raw:>6}{rel:>6}{new:>6}")
            except Exception as exc:  # noqa: BLE001
                print(f"{feed['name']:<22}  FAIL  {type(exc).__name__}: {exc}")
    print("done")


if __name__ == "__main__":
    main()
