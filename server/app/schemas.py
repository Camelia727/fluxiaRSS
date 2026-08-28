"""fluxiaRSS API 契约（占位）。"""
from __future__ import annotations

from datetime import date
from pydantic import BaseModel


class DigestItem(BaseModel):
    article_id: str
    rank: int
    title: str
    summary: str
    url: str
    reason: str
    source: str = ""  # 来源（RSS 源名）；老数据可能缺失，默认空串


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
