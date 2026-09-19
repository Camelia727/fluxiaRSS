"""Zone 分区自测：临时 DB 上验证区配置真正生效（不触碰真实数据）。

跑法：cd server && python zone_selftest.py
"""
from __future__ import annotations

import os
import sys
import tempfile

TMP_DB = os.path.join(tempfile.mkdtemp(prefix="fluxia-zone-test-"), "test.db")
os.environ["FLUXIARSS_DB"] = TMP_DB
os.environ["DEEPSEEK_API_KEY"] = ""      # 概述走回退，不打外网
os.environ["HONCHO_ENABLED"] = "0"
os.environ.pop("FLUXIARSS_ZONES", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.db import (  # noqa: E402
    add_source, create_zone, get_zone, init_db, insert_article,
    list_articles, list_sources, list_zones, remove_source, zone_params,
)
from app.main import app  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + extra) if extra else ""))


def main() -> int:
    init_db()
    client = TestClient(app)

    # ---- 1. 默认区存在且回退全局 ----
    zones = list_zones()
    check("default zone seeded", any(z["id"] == "default" for z in zones),
          str([z["id"] for z in zones]))
    dp = zone_params("default")
    check("default params fall back to global",
          dp["digest_size"] == config.DEFAULT_DIGEST_SIZE
          and dp["fresh_window_hours"] == config.FRESH_WINDOW_HOURS
          and dp["max_age_days"] == config.MAX_AGE_DAYS,
          str(dp))

    # ---- 2. 建创作区：宽年龄窗口 + 小 digest + 独立 honcho ----
    created = create_zone(
        "creator", "创作灵感·海外见闻",
        [{"name": "Waxy", "url": "https://waxy.org/category/links/feed/", "topic": "creator"}],
        [],
        {
            "digest_size": 5,
            "fresh_window_hours": 48,
            "max_age_days": 30,
            "honcho_workspace": "fluxiars_creator",
            "honcho_session": "reading",
            "summarize_topic": "海外互联网见闻与创作灵感，不做技术深度。",
        },
    )
    check("create_zone returns creator", bool(created) and created["id"] == "creator")

    cp = zone_params("creator")
    check("creator digest_size = 5", cp["digest_size"] == 5, str(cp["digest_size"]))
    check("creator fresh_window = 48", cp["fresh_window_hours"] == 48, str(cp["fresh_window_hours"]))
    check("creator max_age_days = 30", cp["max_age_days"] == 30, str(cp["max_age_days"]))
    check("creator honcho workspace isolated",
          cp["honcho_workspace"] == "fluxiars_creator"
          and cp["honcho_workspace"] != dp["honcho_workspace"],
          cp["honcho_workspace"])
    check("creator summarize_topic per-zone",
          cp["summarize_topic"].startswith("海外互联网见闻")
          and cp["summarize_topic"] != dp["summarize_topic"])

    # ---- 3. 源按区隔离 ----
    add_source("https://kottke.org/feed", "Kottke", "creator", zone="creator")
    c_feeds = {f["name"] for f in list_sources(zone="creator")}
    d_feeds = {f["name"] for f in list_sources(zone="default")}
    check("creator feeds isolated", "Kottke" in c_feeds and "Kottke" not in d_feeds,
          str(c_feeds))
    remove_source("https://kottke.org/feed", zone="creator")
    check("remove_source scoped to zone",
          "Kottke" not in {f["name"] for f in list_sources(zone="creator")})

    # ---- 4. 文章按区隔离 + digest 用区内 K ----
    # 每源配额（默认 3）会压同源条目，因此每个区用 3 个源 x 4 篇，
    # 才能验证「K 的截断」而非「每源配额的截断」。
    for i in range(12):
        src = f"src-{i % 3}"
        insert_article(f"c{i:02d}", f"https://ex.com/c{i}", f"创作条目 {i}",
                       src, "创作摘要", "other", zone="creator")
    for i in range(12):
        src = f"dsrc-{i % 3}"
        insert_article(f"d{i:02d}", f"https://ex.com/d{i}", f"技术条目 {i}",
                       src, "技术摘要", "practical", zone="default")
    check("creator articles isolated", len(list_articles(50, zone="creator")) == 12)
    check("default articles isolated", len(list_articles(50, zone="default")) == 12)

    r = client.get("/api/v1/zones/creator/digest")
    check("creator digest 200", r.status_code == 200, str(r.status_code))
    body = r.json()
    check("creator digest respects digest_size=5 (per-zone K)",
          len(body["items"]) == 5, f"got {len(body['items'])}")
    check("creator digest only creator items",
          all("创作" in it["title"] for it in body["items"]))

    r2 = client.get("/api/v1/zones/default/digest")
    check("default digest uses global K=8",
          r2.status_code == 200 and len(r2.json()["items"]) == config.DEFAULT_DIGEST_SIZE,
          f"got {len(r2.json()['items'])}")

    # ---- 5. top 覆盖区配置 ----
    r3 = client.get("/api/v1/zones/creator/digest?top=3")
    check("?top= overrides zone digest_size", len(r3.json()["items"]) == 3)

    # ---- 6. 未知区 404 ----
    r4 = client.get("/api/v1/zones/nope/digest")
    check("unknown zone digest -> 404", r4.status_code == 404, str(r4.status_code))

    # ---- 7. zones_create 用 id 而非 display ----
    r5 = client.post("/api/v1/zones", json={
        "id": "gaming", "display": "游戏向",
        "feeds": [], "keywords": [], "config": {"digest_size": 3},
    })
    check("zones_create 201 with id", r5.status_code == 201, str(r5.status_code) + " " + r5.text[:120])
    if r5.status_code == 201:
        check("zone id is ascii id (not display)", r5.json()["id"] == "gaming", r5.json()["id"])
        check("gaming digest_size=3",
              len(client.get("/api/v1/zones/gaming/digest").json()["items"]) == 0)

    # 缺 id -> 422
    r6 = client.post("/api/v1/zones", json={"display": "无 id"})
    check("zones_create without id -> 422", r6.status_code == 422, str(r6.status_code))

    # 非法 id -> 422
    r7 = client.post("/api/v1/zones", json={"id": "创作区", "display": "x"})
    check("zones_create non-ascii id -> 422", r7.status_code == 422, str(r7.status_code))

    # 重复 id -> 409
    r8 = client.post("/api/v1/zones", json={"id": "creator", "display": "dup"})
    check("duplicate zone -> 409", r8.status_code == 409, str(r8.status_code))

    # ---- 8. 更新：显式空数组可清空 ----
    r9 = client.put("/api/v1/zones/gaming", json={"display": "游戏向v2", "feeds": []})
    check("zones_update 200", r9.status_code == 200, str(r9.status_code))
    g = get_zone("gaming")
    check("zones_update keeps untouched fields",
          g["display"] == "游戏向v2" and g["config"].get("digest_size") == 3,
          str(g))

    # ---- 9. 删除保护 ----
    check("delete existing non-last zone ok",
          client.delete("/api/v1/zones/gaming").status_code == 200)
    check("delete unknown zone -> 404",
          client.delete("/api/v1/zones/gaming").status_code == 404)
    # 删到只剩一个
    client.delete("/api/v1/zones/creator")
    r10 = client.delete("/api/v1/zones/default")
    check("delete last zone -> 409", r10.status_code == 409, str(r10.status_code))

    # ---- 10. 旧端点仍指向 default ----
    r11 = client.get("/api/v1/digest")
    check("legacy /digest still works (default)", r11.status_code == 200)
    r12 = client.get("/api/v1/sources")
    check("legacy /sources still works (default)", r12.status_code == 200)

    print("\n" + "=" * 60)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
