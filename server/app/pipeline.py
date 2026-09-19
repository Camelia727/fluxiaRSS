"""每日采集管道：采集 → 概述 → 落库。"""
from __future__ import annotations

from .collector import collect_all
from .db import init_db, insert_article


def run_pipeline(zone: str = "default") -> dict:
    init_db()
    items = collect_all(zone=zone)
    added = 0
    for item in items:
        if insert_article(
            item["id"], item["url"], item["title"], item["source"],
            item["summary"], item.get("category"), zone=zone,
        ):
            added += 1
    return {"zone": zone, "fetched": len(items), "new_added": added}
