"""fluxiaRSS API —— P1：评分落库 + 排序 digest。"""
from __future__ import annotations

import re
from datetime import date

from fastapi import FastAPI, HTTPException

from . import config
from .db import add_rating, get_article, init_db, list_articles
from .pipeline import run_pipeline
from .ranking import rank_articles
from . import honcho_client
from .schemas import (
    Conclusion,
    Digest,
    DigestItem,
    Profile,
    RatingIn,
    RatingOut,
    SourceInfo,
)

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


@app.get("/api/v1/profile", response_model=Profile)
def get_profile() -> Profile:
    """读取 Honcho 画像（best-effort：未启用/不可用/无数据时返回空画像）。"""
    rep = honcho_client.get_profile()
    return Profile(
        version=1 if rep else 0,
        conclusions=_parse_conclusions(rep),
        representation=rep,
    )


@app.get("/api/v1/sources", response_model=list[SourceInfo])
def get_sources() -> list[SourceInfo]:
    return [
        SourceInfo(name=f["name"], url=f["url"], topic=f["topic"])
        for f in config.FEEDS
    ]

