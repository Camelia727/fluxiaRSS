"""fluxiaRSS -> Honcho 记忆层接入（纯 httpx 调 Honcho REST API，不依赖 honcho SDK）。

REST 端点（v3）：
  POST /v3/workspaces                       get-or-create workspace
  POST /v3/workspaces/{ws}/peers            get-or-create peer
  POST /v3/workspaces/{ws}/sessions         get-or-create session
  POST /v3/workspaces/{ws}/sessions/{s}/messages   添加消息
  POST /v3/workspaces/{ws}/peers/{p}/representation   查询画像（OpenAPI 为 POST）

分区（zone）语义：每个区使用独立的 workspace（区 config 的 honcho_workspace，
缺省回退全局 HONCHO_WORKSPACE_ID）与独立 session（honcho_session）。workspace
的 get-or-create、peer、session、消息、画像查询全部走该区的 workspace，保证
agent 区与创作区的画像互不污染。

全部 best-effort：Honcho 不可用或未启用时静默降级，不影响主流程。

用法（服务器上验证）：.venv/bin/python -m app.honcho_client
"""
from __future__ import annotations

import httpx

from . import config

TIMEOUT = 10


def _params(zone: str) -> dict:
    """该区生效的 Honcho 参数（workspace / session），失败回退全局配置。"""
    try:
        from .db import zone_params
        p = zone_params(zone)
        return {
            "workspace": p.get("honcho_workspace") or config.HONCHO_WORKSPACE_ID,
            "session": p.get("honcho_session") or config.HONCHO_SESSION_ID,
        }
    except Exception:  # noqa: BLE001
        return {
            "workspace": config.HONCHO_WORKSPACE_ID,
            "session": config.HONCHO_SESSION_ID,
        }


def enabled(zone: str = "default") -> bool:
    """该区是否可用 Honcho：总开关开启且该区 workspace 非空。"""
    return config.HONCHO_ENABLED and bool(_params(zone)["workspace"])


def _base() -> str:
    return config.HONCHO_BASE_URL.rstrip("/")


def _ensure_workspace(c: httpx.Client, ws: str) -> None:
    """get-or-create 指定 workspace（按区，而非全局）。"""
    c.post(f"{_base()}/v3/workspaces", json={"id": ws})


def record_rating(article: dict, score: int | None, comment: str | None,
                  action: str, zone: str = "default") -> bool:
    """把一条反馈写入该区的 Honcho session。"""
    if not enabled(zone):
        return False
    try:
        p = _params(zone)
        ws = p["workspace"]
        session_id = p["session"]
        peer = config.HONCHO_PEER_ID
        with httpx.Client(timeout=TIMEOUT) as c:
            _ensure_workspace(c, ws)
            c.post(f"{_base()}/v3/workspaces/{ws}/peers", json={"id": peer})
            c.post(
                f"{_base()}/v3/workspaces/{ws}/sessions",
                json={"id": session_id, "peers": {peer: {}}},
            )
            score_txt = f"评分 {score}/10" if score is not None else "评分：无（仅评论）"
            content = (
                f"阅读反馈：{article.get('title', '')}。{score_txt}。"
                f"评论：{comment or '无'}。动作：{action}。"
                f"来源：{article.get('source', '')}。"
            )
            metadata = {
                "article_id": article.get("id", ""),
                "url": article.get("url", ""),
                "source": article.get("source", ""),
                "score": score,
                "action": action,
                "zone": zone,
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
        print(f"[honcho] record_rating failed (zone={zone}): {exc}")
        return False


def get_profile(zone: str = "default") -> str:
    """查询该区 Honcho 对用户的画像（representation 文本），空串表示不可用。

    真实端点为 POST /v3/workspaces/{ws}/peers/{peer}/representation（不是
    GET），body 全可选、空对象即可，返回 {"representation": str}。
    """
    if not enabled(zone):
        return ""
    try:
        p = _params(zone)
        ws = p["workspace"]
        peer = config.HONCHO_PEER_ID
        with httpx.Client(timeout=TIMEOUT) as c:
            _ensure_workspace(c, ws)
            c.post(f"{_base()}/v3/workspaces/{ws}/peers", json={"id": peer})
            r = c.post(
                f"{_base()}/v3/workspaces/{ws}/peers/{peer}/representation",
                json={},
            )
            r.raise_for_status()
            return r.json().get("representation") or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[honcho] get_profile failed (zone={zone}): {exc}")
        return ""


def probe(zone: str = "default") -> None:
    """端到端验证：写一条测试反馈 + 读画像。"""
    print(f"enabled={enabled(zone)} base={config.HONCHO_BASE_URL} "
          f"zone={zone} ws={_params(zone)['workspace']} "
          f"session={_params(zone)['session']}")
    fake = {"id": "probe-test", "title": "测试文章", "url": "http://x", "source": "test"}
    ok = record_rating(fake, 8, "probe", "read", zone=zone)
    print("record_rating ->", ok)
    profile = get_profile(zone=zone)
    print("get_profile ->", repr(profile))


if __name__ == "__main__":
    import sys
    probe(sys.argv[1] if len(sys.argv) > 1 else "default")
