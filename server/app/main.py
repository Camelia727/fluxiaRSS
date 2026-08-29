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
    get_article,
    get_latest_ratings,
    init_db,
    list_articles,
    list_sources,
    remove_source,
)
from .pipeline import run_pipeline
from .ranking import rank_articles
from . import honcho_client
from . import scheduler
from .schemas import (
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

# 排序候选池大小（先取最近 N 条再排序取 Top）
RANK_POOL = 100


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
    """手动触发采集：抓取→概述→落库。"""
    return run_pipeline()


@app.get("/api/v1/digest", response_model=Digest,
         dependencies=[Depends(require_token)])
def get_digest(d: date | None = None, top: int | None = None) -> Digest:
    """从候选池中按偏好排序，返回 Top K。

    K 由 `?top=` 指定（钳制到 1-50），缺省用配置项 DIGEST_SIZE。
    """
    init_db()
    k = top if top is not None else config.DEFAULT_DIGEST_SIZE
    k = max(1, min(k, 50))
    # 今日新鲜池：只取最近 FRESH_WINDOW_HOURS 内采集到的文章，昨天的自然滑出，
    # 评分/信任只在今日新文内决定排序；空池（当日尚未采集）回退全池保证非空。
    since = (datetime.now(timezone.utc) - timedelta(hours=config.FRESH_WINDOW_HOURS)).isoformat()
    pool = list_articles(RANK_POOL, since=since) or list_articles(RANK_POOL)
    # top_n 显式传 k：rank_articles 内部按 DEFAULT_DIGEST_SIZE 预截断，
    # 不传的话 ?top=20 也拿不到默认 15 条以外的文章。
    ranked = rank_articles(pool, top_n=k)[:k]
    # 跨端同步：取每篇文章最近一次评分，插件据此显示「已评」并避免重复评分
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
            rated=RatedInfo(**latest[r["id"]]) if r["id"] in latest else None,
        )
        for i, r in enumerate(ranked)
    ]
    return Digest(date=d or date.today(), items=items)


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
    """读取 Honcho 画像（best-effort：未启用/不可用/无数据时返回空画像）。"""
    rep = honcho_client.get_profile()
    return Profile(
        version=1 if rep else 0,
        conclusions=_parse_conclusions(rep),
        representation=rep,
    )


@app.get("/api/v1/sources", response_model=list[SourceInfo],
         dependencies=[Depends(require_token)])
def get_sources() -> list[SourceInfo]:
    """当前生效的 RSS 源列表（内置默认 + 用户自定义）。"""
    init_db()
    return [
        SourceInfo(
            name=s["name"], url=s["url"], topic=s["topic"],
            custom=bool(s["custom"]),
        )
        for s in list_sources()
    ]


@app.post("/api/v1/sources", response_model=SourceInfo, status_code=201,
          dependencies=[Depends(require_token)])
def create_source(s: SourceIn) -> SourceInfo:
    """新增/更新一个自定义 RSS 源（按 URL 幂等）。URL 非法返回 422。"""
    init_db()
    src = add_source(s.url, s.name, s.topic)
    if src is None:
        raise HTTPException(status_code=422, detail="invalid url")
    return SourceInfo(
        name=src["name"], url=src["url"], topic=src["topic"], custom=True
    )


@app.delete("/api/v1/sources", dependencies=[Depends(require_token)])
def delete_source(url: str) -> dict:
    """按 URL 删除一个 RSS 源（内置或自定义均可）。不存在返回 404。"""
    init_db()
    if not remove_source(url):
        raise HTTPException(status_code=404, detail="source not found")
    return {"ok": True, "url": url}

