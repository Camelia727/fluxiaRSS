"""fluxiaRSS API —— P1：评分落库 + 排序 digest。"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException

from . import config
from .db import (
    add_rating,
    add_source,
    create_zone,
    delete_zone,
    get_article,
    get_latest_ratings,
    get_zone,
    init_db,
    list_articles,
    list_sources,
    list_zones,
    remove_source,
    update_zone,
    zone_params,
)
from .pipeline import run_pipeline
from .ranking import rank_articles
from . import honcho_client
from . import scheduler
from .schemas import (
    ZoneOut, ZoneDetail, ZoneIn,
    Conclusion,
    Digest,
    DigestItem,
    Profile,
    RatedInfo,
    RatingIn,
    RatingOut,
    SourceIn,
    SourceInfo,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时挂上每日定时采集（每天凌晨 COLLECT_HOUR:COLLECT_MINUTE）。"""
    scheduler.start_scheduler()
    yield
    scheduler.shutdown_scheduler()


app = FastAPI(title="fluxiaRSS API", version="0.2.0", lifespan=lifespan)

# 分区标识约束：小写字母/数字/下划线/短横线，1-32 位（作为 URL 路径段）
_ZONE_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# 排序候选池大小（先取最近 N 条再排序取 Top）
RANK_POOL = 100


def _apply_source_cap(ranked: list[dict], cap: int) -> list[dict]:
    """按来源配额过滤排名列表：每源最多保留前 cap 篇，保持原有排序。

    防止单源（如 arXiv）因量大而霸屏 Top-K；cap<=0 表示不限制。
    """
    if cap <= 0:
        return ranked
    seen: dict[str, int] = {}
    out: list[dict] = []
    for r in ranked:
        src = r.get("source") or "?"
        if seen.get(src, 0) >= cap:
            continue
        seen[src] = seen.get(src, 0) + 1
        out.append(r)
    return out


def _apply_category_cap(ranked: list[dict], cap: int) -> list[dict]:
    """「研究前沿」类（category == RESEARCH_CATEGORY）最多保留 cap 篇，其余照序保留。

    研究类文章被截断后，后续实践/概念文章自然补位，保证 digest 主体是实践内容。
    """
    if cap <= 0:
        return ranked
    seen = 0
    out: list[dict] = []
    for r in ranked:
        if r.get("category") == config.RESEARCH_CATEGORY:
            if seen >= cap:
                continue
            seen += 1
        out.append(r)
    return out


def require_token(x_fluxia_token: str | None = Header(default=None)) -> None:
    """轻量鉴权：配置了 FLUXIARSS_API_TOKEN 时，校验 X-Fluxia-Token 头。"""
    if config.FLUXIARSS_API_TOKEN and x_fluxia_token != config.FLUXIARSS_API_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


@app.get("/health")
def health() -> dict:
    """探活；已跑过定时采集时附上上次 at/result。"""
    h: dict = {"status": "ok"}
    lc = scheduler.last_collection()
    if lc:
        h["last_collected_at"] = lc["at"]
        h["last_collect_result"] = lc["result"]
    return h


@app.post("/api/v1/collect", dependencies=[Depends(require_token)])
def collect() -> dict:
    """向后兼容：等价于 /api/v1/zones/default/collect。"""
    return run_pipeline(zone="default")


@app.get("/api/v1/digest", response_model=Digest,
         dependencies=[Depends(require_token)])
def get_digest(d: date | None = None, top: int | None = None) -> Digest:
    """向后兼容：等价于 /api/v1/zones/default/digest。"""
    return zone_digest("default", d, top)


@app.post("/api/v1/rating", response_model=RatingOut,
          dependencies=[Depends(require_token)])
def post_rating(r: RatingIn) -> RatingOut:
    """持久化评分/评论；文章不存在或评分越界返回 4xx。"""
    if r.score is not None and not (0 <= r.score <= 10):
        raise HTTPException(status_code=422, detail="score 须在 0-10 之间")
    init_db()
    if not add_rating(r.article_id, r.score, r.comment, r.action):
        raise HTTPException(status_code=404, detail="article not found")
    article = get_article(r.article_id)
    if article:
        honcho_client.record_rating(article, r.score, r.comment, r.action)
    return RatingOut(ok=True, article_id=r.article_id)


_HEADER_RE = re.compile(r"^##\s+([A-Za-z_]+)")
_TIMESTAMP_RE = re.compile(r"^\[[^\]]*\]\s*")


def _parse_conclusions(rep: str) -> list[Conclusion]:
    """把 Honcho representation 文本解析成结论列表（best-effort）。

    格式：`## <kind> Observations` 段落后，每行 `[时间戳] 内容`。段落标题
    决定 kind（explicit/deductive/inductive）；解析不匹配或文本为空时返回
    空列表，不做强解析，不影响主流程。
    """
    if not rep:
        return []
    kind = "explicit"
    out: list[Conclusion] = []
    for line in rep.splitlines():
        line = line.strip()
        if not line:
            continue
        header = _HEADER_RE.match(line)
        if header:
            kind = header.group(1).lower()
            continue
        statement = _TIMESTAMP_RE.sub("", line).strip()
        if statement:
            out.append(Conclusion(kind=kind, statement=statement))
    return out


@app.get("/api/v1/profile", response_model=Profile,
         dependencies=[Depends(require_token)])
def get_profile() -> Profile:
    """向后兼容：等价于 /api/v1/zones/default/profile。"""
    return zone_profile("default")


@app.get("/api/v1/sources", response_model=list[SourceInfo],
         dependencies=[Depends(require_token)])
def get_sources() -> list[SourceInfo]:
    """向后兼容：等价于 /api/v1/zones/default/sources。"""
    return zone_sources("default")


@app.post("/api/v1/sources", response_model=SourceInfo, status_code=201,
          dependencies=[Depends(require_token)])
def create_source(s: SourceIn) -> SourceInfo:
    """向后兼容：等价于 POST /api/v1/zones/default/sources。"""
    return zone_add_source("default", s)


@app.delete("/api/v1/sources", dependencies=[Depends(require_token)])
def delete_source(url: str) -> dict:
    """向后兼容：等价于 DELETE /api/v1/zones/default/sources。"""
    return zone_remove_source("default", url)



# ---- Zone CRUD ----

@app.get("/api/v1/zones", dependencies=[Depends(require_token)])
def zones_list():
    """列出所有分区。"""
    init_db()
    zones = list_zones()
    return [
        ZoneOut(
            id=z["id"],
            display=z["display"],
            feed_count=z["feed_count"],
            keyword_count=z["keyword_count"],
            created_at=z["created_at"],
        )
        for z in zones
    ]


@app.get("/api/v1/zones/{zone}", response_model=ZoneDetail,
         dependencies=[Depends(require_token)])
def zones_get(zone: str):
    """查看分区详情。"""
    init_db()
    z = get_zone(zone)
    if not z:
        raise HTTPException(status_code=404, detail="zone not found")
    return ZoneDetail(
        id=z["id"],
        display=z["display"],
        feeds=[SourceInfo(name=f["name"], url=f["url"], topic=f.get("topic","")) for f in z["feeds"]],
        keywords=z["keywords"],
        config=z["config"],
        created_at=z["created_at"],
    )


@app.post("/api/v1/zones", status_code=201, response_model=ZoneDetail,
          dependencies=[Depends(require_token)])
def zones_create(z: ZoneIn):
    """创建新区。id 指定区标识（URL 路径用），display 为展示名。"""
    init_db()
    zone_id = (z.id or "").strip()
    if not _ZONE_ID_RE.match(zone_id):
        raise HTTPException(
            status_code=422,
            detail="id 必填，只能用小写字母/数字/下划线/短横线（1-32 位）",
        )
    result = create_zone(
        zone_id, z.display,
        [{"name": f.name or "", "url": f.url, "topic": f.topic or ""} for f in z.feeds],
        z.keywords, z.config,
    )
    if not result:
        raise HTTPException(status_code=409, detail="zone already exists")
    return zones_get(zone_id)


@app.put("/api/v1/zones/{zone}", response_model=ZoneDetail,
         dependencies=[Depends(require_token)])
def zones_update(zone: str, z: ZoneIn):
    """更新分区配置（部分更新）。

    以请求体里「实际提供了哪些字段」为准（model_fields_set），因此显式传
    空数组可以清空 feeds / keywords，传空对象可以重置 config；未提及的字段
    保持原值。区标识 id 不可变，请求体中的 id 被忽略。
    """
    init_db()
    provided = z.model_fields_set
    result = update_zone(
        zone,
        display=z.display if "display" in provided else None,
        feeds=([{"name": f.name or "", "url": f.url, "topic": f.topic or ""}
                for f in z.feeds] if "feeds" in provided else None),
        keywords=z.keywords if "keywords" in provided else None,
        config_data=z.config if "config" in provided else None,
    )
    if not result:
        raise HTTPException(status_code=404, detail="zone not found")
    return zones_get(zone)


@app.delete("/api/v1/zones/{zone}", dependencies=[Depends(require_token)])
def zones_delete(zone: str):
    """删除分区（及名下所有文章）。最后一个区不允许删。"""
    init_db()
    ok, reason = delete_zone(zone)
    if not ok:
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="zone not found")
        raise HTTPException(status_code=409, detail="不能删除最后一个分区")
    return {"ok": True, "zone": zone}


# ---- Per-zone operation endpoints ----


@app.get("/api/v1/zones/{zone}/digest", response_model=Digest,
         dependencies=[Depends(require_token)])
def zone_digest(zone: str, d: date | None = None, top: int | None = None):
    """某区的 digest：K / 新鲜窗口 / 每源配额 / 研究类上限均按区生效。"""
    init_db()
    if not get_zone(zone):
        raise HTTPException(status_code=404, detail="zone not found")
    p = zone_params(zone)
    k = top if top is not None else p["digest_size"]
    k = max(1, min(k, 50))
    since = (datetime.now(timezone.utc) - timedelta(hours=p["fresh_window_hours"])).isoformat()
    pool = list_articles(RANK_POOL, since=since, zone=zone) or list_articles(RANK_POOL, zone=zone)
    ranked_all = rank_articles(pool, top_n=len(pool) or 1, zone=zone)
    ranked = _apply_source_cap(ranked_all, p["max_per_source"])
    ranked = _apply_category_cap(ranked, p["research_max"])[:k]
    latest = get_latest_ratings([r["id"] for r in ranked]) if ranked else {}
    items = [
        DigestItem(
            article_id=r["id"],
            rank=i + 1,
            title=r["title"],
            summary=r["summary"] or "",
            url=r["url"],
            reason=r["reason"],
            source=r["source"] or "",
            category=r.get("category") or "other",
            rated=RatedInfo(**latest[r["id"]]) if r["id"] in latest else None,
        )
        for i, r in enumerate(ranked)
    ]
    return Digest(date=d or date.today(), items=items)


@app.post("/api/v1/zones/{zone}/collect", dependencies=[Depends(require_token)])
def zone_collect(zone: str):
    """手动触发某区采集。"""
    return run_pipeline(zone=zone)


@app.post("/api/v1/zones/{zone}/rating", response_model=RatingOut,
          dependencies=[Depends(require_token)])
def zone_rating(zone: str, r: RatingIn):
    """记录某区评分。"""
    if r.score is not None and not (0 <= r.score <= 10):
        raise HTTPException(status_code=422, detail="score must be 0-10")
    init_db()
    if not add_rating(r.article_id, r.score, r.comment, r.action):
        raise HTTPException(status_code=404, detail="article not found")
    article = get_article(r.article_id)
    if article:
        honcho_client.record_rating(article, r.score, r.comment, r.action, zone=zone)
    return RatingOut(ok=True, article_id=r.article_id)


@app.get("/api/v1/zones/{zone}/profile", response_model=Profile,
         dependencies=[Depends(require_token)])
def zone_profile(zone: str):
    """读取某区的 Honcho 画像。"""
    rep = honcho_client.get_profile(zone=zone)
    return Profile(
        version=1 if rep else 0,
        conclusions=_parse_conclusions(rep),
        representation=rep,
    )


@app.get("/api/v1/zones/{zone}/sources", response_model=list[SourceInfo],
         dependencies=[Depends(require_token)])
def zone_sources(zone: str):
    """某区的 RSS 源列表。"""
    init_db()
    feeds = list_sources(zone=zone)
    return [
        SourceInfo(name=f["name"], url=f["url"], topic=f.get("topic", ""), custom=True)
        for f in feeds
    ]


@app.post("/api/v1/zones/{zone}/sources", response_model=SourceInfo, status_code=201,
          dependencies=[Depends(require_token)])
def zone_add_source(zone: str, s: SourceIn):
    """为某区添加 RSS 源。"""
    init_db()
    src = add_source(s.url, s.name, s.topic, zone=zone)
    if src is None:
        raise HTTPException(status_code=422, detail="invalid url")
    return SourceInfo(name=src["name"], url=src["url"], topic=src.get("topic", ""), custom=True)


@app.delete("/api/v1/zones/{zone}/sources", dependencies=[Depends(require_token)])
def zone_remove_source(zone: str, url: str):
    """从某区移除 RSS 源。"""
    init_db()
    if not remove_source(url, zone=zone):
        raise HTTPException(status_code=404, detail="source not found")
    return {"ok": True, "url": url}

