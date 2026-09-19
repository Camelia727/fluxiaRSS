"""fluxiaRSS API 契约。"""
from __future__ import annotations

from datetime import date
from typing import Any
from pydantic import BaseModel


class RatedInfo(BaseModel):
    """当前用户对某篇文章最近一次的评分（未评过则为 None）。"""
    score: int | None = None  # 打分 0-10
    action: str = "read"  # read | skip | later | comment
    comment: str | None = None


class DigestItem(BaseModel):
    article_id: str
    rank: int
    title: str
    summary: str
    url: str
    reason: str
    source: str = ""
    category: str = "other"
    rated: RatedInfo | None = None


class Digest(BaseModel):
    date: date
    items: list[DigestItem]


class RatingIn(BaseModel):
    article_id: str
    score: int | None = None
    comment: str | None = None
    action: str = "read"


class RatingOut(BaseModel):
    ok: bool
    article_id: str


class Conclusion(BaseModel):
    kind: str
    statement: str
    confidence: float | None = None


class Profile(BaseModel):
    version: int
    conclusions: list[Conclusion]
    representation: str = ""


class SourceInfo(BaseModel):
    name: str
    url: str
    topic: str
    custom: bool = False


class SourceIn(BaseModel):
    url: str
    name: str | None = None
    topic: str | None = None


# -- Zone 模型 --

class ZoneOut(BaseModel):
    """区视图（列表场景）。"""
    id: str
    display: str
    feed_count: int = 0
    keyword_count: int = 0
    created_at: str


class ZoneDetail(BaseModel):
    """区详情（含 feeds / keywords / config）。"""
    id: str
    display: str
    feeds: list[SourceInfo] = []
    keywords: list[str] = []
    config: dict[str, Any] = {}
    created_at: str


class ZoneIn(BaseModel):
    """创建/更新区的请求体。

    id 仅创建时必填（小写字母/数字/下划线/短横线），作为 URL 路径里的区标识；
    更新时忽略该字段（区标识不可变）。display 为展示名，可与 id 不同。
    """
    id: str | None = None
    display: str
    feeds: list[SourceIn] = []
    keywords: list[str] = []
    config: dict[str, Any] = {}
