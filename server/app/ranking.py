"""fluxiaRSS 规则版排序（P1 占位；Honcho 接入后升级为结论驱动）。

score = w_r*avg_rating + w_t*(source_trust-5) + w_rec*recency
初始无评分时 trust 取中性 5 → (trust-5)=0，主要由 recency 驱动；
一旦有评分/来源数据，评分与来源信任开始影响排序。
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import config
from .db import get_article_avg_rating, source_trust

W_RATING = 0.60
W_TRUST = 0.25
W_RECENCY = 0.15


def _recency(fetched_at: str) -> float:
    try:
        dt = datetime.fromisoformat(fetched_at)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
        return max(0.0, 1.0 - age_days / 7.0)
    except Exception:  # noqa: BLE001
        return 0.5


def _score(article: dict, avg_rating: float, trust: float) -> float:
    return W_RATING * avg_rating + W_TRUST * (trust - 5.0) + W_RECENCY * _recency(
        article["fetched_at"]
    )


def rank_articles(articles: list[dict], top_n: int | None = None) -> list[dict]:
    """返回按 score 降序、附 score/reason 的文章。"""
    top_n = top_n or config.DEFAULT_DIGEST_SIZE
    trust = source_trust()
    scored = []
    for a in articles:
        avg_r = get_article_avg_rating(a["id"])
        t = trust.get(a["source"], 5.0)
        s = _score(a, avg_r, t)
        reason_parts = []
        if avg_r > 0:
            reason_parts.append(f"评分 {avg_r:.1f}")
        if t != 5.0:
            reason_parts.append(f"来源信任 {t:.1f}")
        if not reason_parts:
            reason_parts.append("较新")
        a = dict(a)
        a["score"] = round(s, 3)
        a["reason"] = " · ".join(reason_parts)
        scored.append(a)
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_n]
