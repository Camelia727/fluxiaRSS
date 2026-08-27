"""每日采集管道：采集 → 概述 → 落库。"""
from __future__ import annotations

from .collector import collect_all
from .db import init_db, insert_article


def run_pipeline() -> dict:
    init_db()
    items = collect_all()
    added = 0
    for item in items:
        if insert_article(
            item["id"], item["url"], item["title"], item["source"], item["summary"]
        ):
            added += 1
    return {"fetched": len(items), "new_added": added}
