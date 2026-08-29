"""fluxiaRSS API 契约（占位）。"""
from __future__ import annotations

from datetime import date
from pydantic import BaseModel


class RatedInfo(BaseModel):
    """当前用户对某篇文章最近一次的评分（未评过则为 None）。"""
    score: int | None = None  # 打分 0-10；稍后读/跳过为 None
    action: str = "read"  # read | skip | later | comment
    comment: str | None = None


class DigestItem(BaseModel):
    article_id: str
    rank: int
    title: str
    summary: str
    url: str
    reason: str
    source: str = ""  # 来源（RSS 源名）；老数据可能缺失，默认空串
    category: str = "other"  # research/practical/news/other；老数据可能缺失
    rated: RatedInfo | None = None  # 跨库/跨端同步：该文章最近一次评分


class Digest(BaseModel):
    date: date
    items: list[DigestItem]


class RatingIn(BaseModel):
    article_id: str
    score: int | None = None
    comment: str | None = None
    action: str = "read"  # read | skip | later | comment


class RatingOut(BaseModel):
    ok: bool
    article_id: str


class Conclusion(BaseModel):
    kind: str  # explicit | deductive | inductive
    statement: str
    confidence: float | None = None


class Profile(BaseModel):
    version: int
    conclusions: list[Conclusion]
    representation: str = ""  # Honcho 画像原文（best-effort）


class SourceInfo(BaseModel):
    name: str
    url: str
    topic: str
    custom: bool = False  # True=用户自定义，False=内置默认


class SourceIn(BaseModel):
    url: str
    name: str | None = None  # 留空则用域名
    topic: str | None = None  # 留空则 "custom"
