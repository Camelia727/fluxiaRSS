"""在服务器上运行：逐源测试 v1 采集器拉取是否正常。
用法：cd /opt/fluxiars && .venv/bin/python pull_test.py
"""
from __future__ import annotations

import httpx
import feedparser

from app import config
from app.collector import _entry_age_days, _hash
from app.db import get_article, init_db, list_sources
from app.relevance import relevant


def main() -> None:
    init_db()
    print(f"{'SOURCE':<22}{'RAW':>6}{'WIN':>6}{'KW':>6}{'NEW':>6}")
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        for feed in list_sources():
            try:
                text = client.get(feed["url"]).text
                parsed = feedparser.parse(text)
                raw = len(parsed.entries)
                win = kw = new = 0
                for entry in parsed.entries[: config.PER_FEED_CAP * 3]:
                    title = getattr(entry, "title", "") or ""
                    desc = (
                        getattr(entry, "summary", "")
                        or getattr(entry, "description", "")
                        or ""
                    )
                    link = getattr(entry, "link", "") or ""
                    age = _entry_age_days(entry)
                    # 按源放宽年龄窗口（与 collector 一致）；关键词只计软信号命中数，
                    # 不再硬过滤（KW 列反映旧硬过滤会挡掉多少候选）
                    max_age = config.SOURCE_MAX_AGE_DAYS.get(
                        feed["name"], config.MAX_AGE_DAYS
                    )
                    if age is not None and age > max_age:
                        continue
                    win += 1
                    if relevant(title, desc):
                        kw += 1
                    if link and not get_article(_hash(link)):
                        new += 1
                print(f"{feed['name']:<22}{raw:>6}{win:>6}{kw:>6}{new:>6}")
            except Exception as exc:  # noqa: BLE001
                print(f"{feed['name']:<22}  FAIL  {type(exc).__name__}: {exc}")
    print("done")


if __name__ == "__main__":
    main()
