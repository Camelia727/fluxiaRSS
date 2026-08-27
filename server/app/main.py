"""fluxiaRSS API —— P1：评分落库 + 排序 digest。"""
from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException

from . import config
from .db import add_rating, init_db, list_articles
from .pipeline import run_pipeline
from .ranking import rank_articles
from .schemas import Digest, DigestItem, Profile, RatingIn, RatingOut, SourceInfo

app = FastAPI(title="fluxiaRSS API", version="0.2.0")

# 排序候选池大小（先取最近 N 条再排序取 Top）
RANK_POOL = 100


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/collect")
def collect() -> dict:
    """手动触发采集：抓取→概述→落库。"""
    return run_pipeline()


@app.get("/api/v1/digest", response_model=Digest)
def get_digest(d: date | None = None) -> Digest:
    """从候选池中按偏好排序，返回 Top N。"""
    init_db()
    ranked = rank_articles(list_articles(RANK_POOL))
    items = [
        DigestItem(
            article_id=r["id"],
            rank=i + 1,
            title=r["title"],
            summary=r["summary"] or "",
            url=r["url"],
            reason=r["reason"],
        )
        for i, r in enumerate(ranked)
    ]
    return Digest(date=d or date.today(), items=items)


@app.post("/api/v1/rating", response_model=RatingOut)
def post_rating(r: RatingIn) -> RatingOut:
    """持久化评分/评论；文章不存在或评分越界返回 4xx。"""
    if r.score is not None and not (0 <= r.score <= 10):
        raise HTTPException(status_code=422, detail="score 须在 0-10 之间")
    init_db()
    if not add_rating(r.article_id, r.score, r.comment, r.action):
        raise HTTPException(status_code=404, detail="article not found")
    return RatingOut(ok=True, article_id=r.article_id)


@app.get("/api/v1/profile", response_model=Profile)
def get_profile() -> Profile:
    """占位：返回空画像（P2 接入 Honcho）。"""
    return Profile(version=0, conclusions=[])


@app.get("/api/v1/sources", response_model=list[SourceInfo])
def get_sources() -> list[SourceInfo]:
    return [
        SourceInfo(name=f["name"], url=f["url"], topic=f["topic"])
        for f in config.FEEDS
    ]
