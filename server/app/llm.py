"""DeepSeek 概述适配器（OpenAI 兼容，plain chat，P0 无结构化输出）。"""
from __future__ import annotations

import os

import httpx

from . import config


def summarize(title: str, desc: str) -> str:
    """生成中文要点概述；无 API key 或失败时回退到原文摘要。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return (desc or title).strip()[:400] or title

    prompt = (
        "用中文为下面这篇文章写 3 条要点概述（每条一行、简洁、信息密度高）：\n\n"
        f"标题：{title}\n\n正文/摘要：{desc[:2000]}"
    )
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 300,
    }
    try:
        r = httpx.post(
            f"{config.DEEPSEEK_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=config.DEEPSEEK_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        return f"(概述失败: {exc}) " + (desc or title)[:200]
