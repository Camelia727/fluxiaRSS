"""fluxiaRSS 服务端配置 —— 从环境变量读取，密钥不入库。

复制 server/.env.example 为 .env 并按需修改；运行前加载环境变量。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# 显式加载 server 目录下的 .env（含密钥，不入库）；override=True 让 .env 成为权威
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

APP_NAME = os.getenv("FLUXIARSS_APP_NAME", "fluxiaRSS")
API_PREFIX = "/api/v1"
DEFAULT_DIGEST_SIZE = int(os.getenv("DIGEST_SIZE", "8"))
# digest 候选池只取最近 FRESH_WINDOW_HOURS 小时内采集到的文章（今日新鲜池）；
# 空池时回退全池，保证 digest 非空
FRESH_WINDOW_HOURS = int(os.getenv("FRESH_WINDOW_HOURS", "24"))
EXPLORATION_BUDGET = float(os.getenv("EXPLORATION_BUDGET", "0.15"))

# 数据库（本地文件）
DB_PATH = os.getenv("FLUXIARSS_DB", "fluxiars.db")

# 话题与源（可在本地/部署时按需调整）
# 采集相关性关键词（标题/摘要命其一即放行）。中英混排：
# - agent 系保持窄口径（用户主兴趣）
# - 更宽的 AI 词让 arXiv 之外的英文源（如 The Verge）能进池
# - 中文词让中文源（量子位/阮一峰）能进池
# 匹配规则见 collector._relevant：英文词整词匹配（兼容 agents/llms 词尾），
# 中文子串匹配，大小写不敏感；"ai" 用整词匹配所以不会误命中 said/available。
KEYWORDS = [
    "agent", "ai agent", "agent framework", "llm agent", "model release",
    # 更宽的英文 AI 词
    "llm", "gpt", "openai", "chatgpt", "ai", "artificial intelligence",
    "machine learning", "deep learning", "neural network",
    "large language model", "generative",
    # agent 应用层（软信号核心，采集加权 + 排序加成）
    "mcp", "model context protocol",
    # 中文 AI 词
    "大模型", "人工智能", "智能体", "提示词", "模型",
]

# 采集请求的 User-Agent：部分 RSS 源（如 qbitai）会拦截默认 UA
FEED_USER_AGENT = os.getenv(
    "FEED_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

# digest 每源配额：排名后每源最多保留这么多篇，防止单源（如 arXiv）霸屏 Top-K；
# 0 表示不限制
DIGEST_MAX_PER_SOURCE = int(os.getenv("DIGEST_MAX_PER_SOURCE", "3"))

# 「研究前沿」类文章在 digest 里的硬上限：分类为 RESEARCH_CATEGORY 的文章
# 最多保留这么多篇，其余位置由实践/概念等文章填充（Obsidian 端可再调 TOPN）。
RESEARCH_CATEGORY = "research"
RESEARCH_MAX_IN_DIGEST = int(os.getenv("RESEARCH_MAX_IN_DIGEST", "2"))

# API 轻量鉴权：设置了该令牌时，/api/v1/* 请求需带 X-Fluxia-Token 头；
# 为空则不做校验（本地调试）。/health 始终公开，用于连通性探测。
FLUXIARSS_API_TOKEN = os.getenv("FLUXIARSS_API_TOKEN", "")

FEEDS = [
    {"name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/", "topic": "agent"},
    {"name": "Latent Space", "url": "https://www.latent.space/feed", "topic": "agent"},
    {"name": "Ben's Bites", "url": "https://www.bensbites.com/feed.xml", "topic": "agent"},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "topic": "agent"},
    {"name": "Cloudflare AI", "url": "https://blog.cloudflare.com/tag/ai/rss/", "topic": "agent"},
    {"name": "Vercel Blog", "url": "https://vercel.com/blog/rss.xml", "topic": "agent"},
]

# 采集限制：单源单轮最多新增条数（防 arXiv 等大源刷库）
PER_FEED_CAP = int(os.getenv("PER_FEED_CAP", "20"))
# 采集扫描上限：单源单轮最多评估的 entry 数（需大于 PER_FEED_CAP 才能做偏好挑选）
PER_FEED_SCAN_CAP = int(os.getenv("PER_FEED_SCAN_CAP", "100"))
# 采集时效：按发布时间超过该天数的旧文直接跳过（防 RSS 源回吐历史文章）
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "2"))
# 按源放宽年龄窗口：慢更新源（如 Cloudflare AI / Vercel Blog）给更长窗口，
# 否则 MAX_AGE_DAYS=2 会把它们的内容几乎全挡掉。默认字典可被 env JSON
# SOURCE_MAX_AGE_DAYS 整体覆盖，如 '{"Cloudflare AI": 60}'
try:
    _source_age = os.getenv("SOURCE_MAX_AGE_DAYS", "")
    SOURCE_MAX_AGE_DAYS = json.loads(_source_age) if _source_age else {
        "Cloudflare AI": 30,
        "Vercel Blog": 30,
    }
except (ValueError, json.JSONDecodeError):
    SOURCE_MAX_AGE_DAYS = {"Cloudflare AI": 30, "Vercel Blog": 30}

# 评分参与采集筛选（混合力度）：
# - 来源门控：均分 < SOURCE_MIN_TRUST 且评分 >= SOURCE_MIN_RATINGS 条的源，整轮跳过
# - 内容偏好：正偏好词（高分标题）/关键词命中加权；反偏好词（低分标题）命中且
#   无正偏好也无关键词命中才硬删（软信号：低分流到 ranking 的 avg_rating 压排名）
# - 探索保底：每源名额中保 EXPLORATION_BUDGET 比例给非偏好文章（防越筛越窄）
SOURCE_MIN_TRUST = float(os.getenv("SOURCE_MIN_TRUST", "4.0"))
SOURCE_MIN_RATINGS = int(os.getenv("SOURCE_MIN_RATINGS", "3"))

# 排序时关键词命中的加成（软信号，低于 Honcho 画像加成的 0.5）：
# 命中 KEYWORDS 的文章在 digest 里 +KEYWORD_HIT_BONUS 并标注「主题相关」。
KEYWORD_HIT_BONUS = float(os.getenv("KEYWORD_HIT_BONUS", "0.3"))

# 概述时注入的用户主题定位（软约束，引导 LLM 概述贴合主题，不硬过滤）
SUMMARIZE_TOPIC = os.getenv(
    "SUMMARIZE_TOPIC",
    "AI 智能体应用与前沿 agent 开发实践，关注 Agent 框架、MCP 生态、"
    "AI 应用落地；对模型底层 RL/调参不感兴趣。",
)
# Honcho 画像注入概述 prompt 的最大长度（字符）
PROFILE_MAX_CHARS = int(os.getenv("PROFILE_MAX_CHARS", "500"))

# 概述并发数（凌晨定时任务，8 并发足够）
SUMMARY_WORKERS = int(os.getenv("SUMMARY_WORKERS", "8"))

# 定时采集（cron，本地时间，每天自动跑一次 run_pipeline）
COLLECT_HOUR = int(os.getenv("COLLECT_HOUR", "3"))
COLLECT_MINUTE = int(os.getenv("COLLECT_MINUTE", "17"))

# DeepSeek（OpenAI 兼容）
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT = int(os.getenv("DEEPSEEK_TIMEOUT", "40"))

# Honcho（记忆层）
HONCHO_ENABLED = os.getenv("HONCHO_ENABLED", "0") == "1"
HONCHO_BASE_URL = os.getenv("HONCHO_BASE_URL", "http://localhost:8001")
HONCHO_WORKSPACE_ID = os.getenv("HONCHO_WORKSPACE_ID", "")
HONCHO_PEER_ID = os.getenv("HONCHO_PEER_ID", "user")
HONCHO_SESSION_ID = os.getenv("HONCHO_SESSION_ID", "reading")





