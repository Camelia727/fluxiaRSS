"""fluxiaRSS → Honcho 记忆层接入。

- record_rating: 把一条评分/评论写成 Honcho session 消息（后台 deriver 会归纳成画像）
- get_profile: 查询 Honcho 对用户的画像（card），供排序注入
- 全部 best-effort：Honcho 不可用或未启用时静默降级，不影响主流程

用法（服务器上验证）：
  .venv/bin/python -m app.honcho_client
"""
from __future__ import annotations

from . import config


def _client():
    from honcho import Honcho  # 惰性导入，未启用时不影响其它模块

    return Honcho(
        base_url=config.HONCHO_BASE_URL, workspace_id=config.HONCHO_WORKSPACE_ID
    )


def enabled() -> bool:
    return config.HONCHO_ENABLED and bool(config.HONCHO_WORKSPACE_ID)


def record_rating(article: dict, score: int | None, comment: str | None,
                  action: str) -> bool:
    """把一条反馈写入 Honcho session。"""
    if not enabled():
        return False
    try:
        from honcho import MessageCreateParams

        client = _client()
        user = client.peer(config.HONCHO_PEER_ID)
        session = client.session(config.HONCHO_SESSION_ID, peers=[user.id])
        content = (
            f"阅读反馈：{article.get('title','')}。"
            f"评分 {score}/10。评论：{comment or '无'}。动作：{action}。"
            f"来源：{article.get('source','')}。"
        )
        metadata = {
            "article_id": article.get("id", ""),
            "url": article.get("url", ""),
            "source": article.get("source", ""),
            "score": score,
            "action": action,
        }
        session.add_messages(
            MessageCreateParams(content=content, peer_id=user.id, metadata=metadata)
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[honcho] record_rating failed: {exc}")
        return False


def get_profile() -> str:
    """查询 Honcho 对用户的画像（card 文本），为空串表示不可用。"""
    if not enabled():
        return ""
    try:
        client = _client()
        user = client.peer(config.HONCHO_PEER_ID)
        card = user.card() or []
        return "\n".join(card)
    except Exception as exc:  # noqa: BLE001
        print(f"[honcho] get_profile failed: {exc}")
        return ""


def probe() -> None:
    """服务器端端到端验证：写一条测试反馈 + 读画像。"""
    print(f"enabled={enabled()} base={config.HONCHO_BASE_URL} "
          f"ws={config.HONCHO_WORKSPACE_ID}")
    fake_article = {"id": "probe-test", "title": "测试文章", "url": "http://x",
                    "source": "test"}
    ok = record_rating(fake_article, 8, "probe", "read")
    print("record_rating ->", ok)
    profile = get_profile()
    print("get_profile ->", repr(profile))


if __name__ == "__main__":
    probe()
