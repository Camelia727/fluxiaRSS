"""fluxiaRSS 规则版排序（P1 占位；Honcho 接入后升级为结论驱动）。

score = w_r*avg_rating + w_t*(source_trust-5) + w_rec*recency [+ 画像命中加成 + 关键词加成]
初始无评分时 trust 取中性 5 → (trust-5)=0，主要由 recency 驱动。
Honcho 启用时，把画像关键词作为小加成注入排序；关键词命中再加 KEYWORD_HIT_BONUS。
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import config
from .db import get_article_avg_rating, source_trust
from .relevance import relevant

W_RATING = 0.60
W_TRUST = 0.25
W_RECENCY = 0.15
PROFILE_HIT_BONUS = 0.5


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


def _profile_tokens() -> set[str]:
    """从 Honcho 画像取关键词；不可用返回空集。

    画像原文是 conclusions 原样拼接（含时间戳、标点残留、英文停用词），直接 split
    会让画像命中几乎必然命中、+0.5 加成失去区分度；用 db 的停用词表过滤噪声。
    """
    from .db import _STOPWORDS
    from .honcho_client import get_profile

    try:
        profile = get_profile()
    except Exception:  # noqa: BLE001
        return set()
    return {
        w
        for w in profile.lower().split()
        if w.isalpha() and len(w) > 2 and w not in _STOPWORDS
    }


def rank_articles(articles: list[dict], top_n: int | None = None) -> list[dict]:
    """返回按 score 降序、附 score/reason 的文章。

    画像命中 / 关键词命中都**只判 title**（不判 summary）：概述 prompt 注入了
    主题定位与画像，若按 summary 匹配会让每篇被概述的文章都制造伪命中，
    形成自证循环。关键词加成低于画像加成（0.3 < 0.5），不会压过真实评分。
    """
    top_n = top_n or config.DEFAULT_DIGEST_SIZE
    trust = source_trust()
    tokens = _profile_tokens() if config.HONCHO_ENABLED else set()
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
        hit = 0.0
        # 关键词软信号（只判 title）：命中加 KEYWORD_HIT_BONUS，reason 标注
        if relevant(a["title"], ""):
            hit += config.KEYWORD_HIT_BONUS
            reason_parts.append("主题相关")
        if tokens and any(tok in a["title"].lower() for tok in tokens):
            hit += PROFILE_HIT_BONUS
            reason_parts.append("画像命中")
        if not reason_parts:
            reason_parts.append("较新")
        a = dict(a)
        a["score"] = round(s + hit, 3)
        a["reason"] = " · ".join(reason_parts)
        scored.append(a)
    # 确定性 tiebreak：score 相同按 fetched_at 升序、id 升序
    # （概述并发返回顺序不定，避免同分时 digest 排序抖动）
    scored.sort(key=lambda x: (-x["score"], x["fetched_at"], x["id"]))
    return scored[:top_n]
