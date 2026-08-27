"""fluxiaRSS → Honcho 记忆层接入（纯 httpx 调 Honcho REST API，不依赖 honcho SDK）。

REST 端点（v3）：
  POST /v3/workspaces                       get-or-create workspace
  POST /v3/workspaces/{ws}/peers            get-or-create peer
  POST /v3/workspaces/{ws}/sessions         get-or-create session
  POST /v3/workspaces/{ws}/sessions/{s}/messages   添加消息
  POST /v3/workspaces/{ws}/peers/{p}/representation   查询画像（OpenAPI 为 POST）

全部 best-effort：Honcho 不可用或未启用时静默降级，不影响主流程。

用法（服务器上验证）：.venv/bin/python -m app.honcho_client
"""
from __future__ import annotations

import httpx

from . import config

TIMEOUT = 10


def enabled() -> bool:
    return config.HONCHO_ENABLED and bool(config.HONCHO_WORKSPACE_ID)


def _base() -> str:
    return config.HONCHO_BASE_URL.rstrip("/")


def _ensure_workspace(c: httpx.Client) -> None:
    c.post(f"{_base()}/v3/workspaces", json={"id": config.HONCHO_WORKSPACE_ID})


def record_rating(article: dict, score: int | None, comment: str | None,
                  action: str) -> bool:
    """把一条反馈写入 Honcho session。"""
    if not enabled():
        return False
    try:
        ws = config.HONCHO_WORKSPACE_ID
        peer = config.HONCHO_PEER_ID
        session_id = config.HONCHO_SESSION_ID
        with httpx.Client(timeout=TIMEOUT) as c:
            _ensure_workspace(c)
            c.post(f"{_base()}/v3/workspaces/{ws}/peers", json={"id": peer})
            c.post(
                f"{_base()}/v3/workspaces/{ws}/sessions",
                json={"id": session_id, "peers": {peer: {}}},
            )
            content = (
                f"阅读反馈：{article.get('title', '')}。"
                f"评分 {score}/10。评论：{comment or '无'}。动作：{action}。"
                f"来源：{article.get('source', '')}。"
            )
            metadata = {
                "article_id": article.get("id", ""),
                "url": article.get("url", ""),
                "source": article.get("source", ""),
                "score": score,
                "action": action,
            }
            r = c.post(
                f"{_base()}/v3/workspaces/{ws}/sessions/{session_id}/messages",
                json={
                    "messages": [
                        {"content": content, "peer_id": peer, "metadata": metadata}
                    ]
                },
            )
            r.raise_for_status()
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"[honcho] record_rating failed: {exc}")
        return False


def get_profile() -> str:
    """查询 Honcho 对用户的画像（representation 文本），空串表示不可用。

    真实端点为 POST /v3/workspaces/{ws}/peers/{peer}/representation（不是
    GET），body 全可选、空对象即可，返回 {"representation": str}。
    """
    if not enabled():
        return ""
    try:
        ws = config.HONCHO_WORKSPACE_ID
        peer = config.HONCHO_PEER_ID
        with httpx.Client(timeout=TIMEOUT) as c:
            _ensure_workspace(c)
            c.post(f"{_base()}/v3/workspaces/{ws}/peers", json={"id": peer})
            r = c.post(
                f"{_base()}/v3/workspaces/{ws}/peers/{peer}/representation",
                json={},
            )
            r.raise_for_status()
            return r.json().get("representation") or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[honcho] get_profile failed: {exc}")
        return ""


def probe() -> None:
    """服务器端到端验证：写一条测试反馈 + 读画像。"""
    print(f"enabled={enabled()} base={config.HONCHO_BASE_URL} "
          f"ws={config.HONCHO_WORKSPACE_ID}")
    fake = {"id": "probe-test", "title": "测试文章", "url": "http://x", "source": "test"}
    ok = record_rating(fake, 8, "probe", "read")
    print("record_rating ->", ok)
    profile = get_profile()
    print("get_profile ->", repr(profile))


if __name__ == "__main__":
    probe()
