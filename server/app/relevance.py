"""关键词相关性判定（纯函数，采集与排序共用）。

供 collector 采集加权与 ranking 排序加成使用。只依赖 config.KEYWORDS，
不引入采集器/排序器的依赖，避免 import 循环。
"""
from __future__ import annotations

import re

from . import config


def relevant(title: str, desc: str, keywords: list[str] | None = None) -> bool:
    """关键词相关性：英文词整词匹配（允许常见词尾），中文子串匹配。

    大小写不敏感；英文用词边界避免 "ai" 误命中 said/available/air 等普通词，
    只允许 s/es/ing/ed/ly 常见词尾，避免 "ai" 命中 Airtable 这类以 ai 开头的词。
    """
    low = f"{title} {desc}".lower()
    kws = keywords if keywords is not None else config.KEYWORDS
    for kw in kws:
        k = kw.lower()
        if k.isascii():
            if re.search(rf"\b{re.escape(k)}(?:s|es|ing|ed|ly)?\b", low):
                return True
        elif k in low:
            return True
    return False
