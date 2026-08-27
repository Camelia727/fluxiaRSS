"""fluxiaRSS API —— P0：占位 + 采集/概述/digest 已接线。"""
from __future__ import annotations

from datetime import date

from fastapi import FastAPI

from . import config
from .db import init_db, list_articles
from .pipeline import run_pipeline
from .schemas import Digest, DigestItem, Profile, RatingIn, RatingOut, SourceInfo

app = FastAPI(title="fluxiaRSS API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/collect")
def collect() -> dict:
    """手动触发采集：抓取→概述→落库。"""
    return run_pipeline()


@app.get("/api/v1/digest", response_model=Digest)
def get_digest(d: date | None = None) -> Digest:
    """返回库中最近文章（P0 按时间倒序；排序在 P1/P2 接入）。"""
    init_db()
    rows = list_articles(config.DEFAULT_DIGEST_SIZE)
    items = [
        DigestItem(
            article_id=r["id"],
            rank=i + 1,
            title=r["title"],
            summary=r["summary"] or "",
            url=r["url"],
            reason="recent",
        )
        for i, r in enumerate(rows)
    ]
    return Digest(date=d or date.today(), items=items)


@app.post("/api/v1/rating", response_model=RatingOut)
def post_rating(r: RatingIn) -> RatingOut:
    """占位：接收评分，未落库（P2 接入）。"""
    return RatingOut(ok=True, article_id=r.article_id)


@app.get("/api/v1/profile", response_model=Profile)
def get_profile() -> Profile:
    """占位：返回空画像。"""
    return Profile(version=0, conclusions=[])


@app.get("/api/v1/sources", response_model=list[SourceInfo])
def get_sources() -> list[SourceInfo]:
    return [
        SourceInfo(name=f["name"], url=f["url"], topic=f["topic"])
        for f in config.FEEDS
    ]
