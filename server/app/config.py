"""fluxiaRSS 服务端配置 —— 从环境变量读取，密钥不入库。

复制 server/.env.example 为 .env 并按需修改；运行前加载环境变量。
"""
from __future__ import annotations

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
KEYWORDS = ["agent", "ai agent", "agent framework", "llm agent", "model release", "agents"]

# API 轻量鉴权：设置了该令牌时，/api/v1/* 请求需带 X-Fluxia-Token 头；
# 为空则不做校验（本地调试）。/health 始终公开，用于连通性探测。
FLUXIARSS_API_TOKEN = os.getenv("FLUXIARSS_API_TOKEN", "")

FEEDS = [
    {"name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/", "topic": "agent"},
    {"name": "Latent Space", "url": "https://www.latent.space/feed", "topic": "agent"},
    {"name": "Lilian Weng", "url": "https://lilianweng.github.io/index.xml", "topic": "agent"},
    {"name": "arXiv cs.AI", "url": "https://export.arxiv.org/rss/cs.AI", "topic": "agent"},
    {"name": "arXiv cs.LG", "url": "https://export.arxiv.org/rss/cs.LG", "topic": "agent"},
    {"name": "Ben's Bites", "url": "https://www.bensbites.com/feed.xml", "topic": "agent"},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "topic": "agent"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "topic": "agent"},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "topic": "agent"},
]

# 采集限制：单源单轮最多新增条数（防 arXiv 等大源刷库）
PER_FEED_CAP = int(os.getenv("PER_FEED_CAP", "20"))
# 采集扫描上限：单源单轮最多评估的 entry 数（需大于 PER_FEED_CAP 才能做偏好挑选）
PER_FEED_SCAN_CAP = int(os.getenv("PER_FEED_SCAN_CAP", "100"))
# 采集时效：按发布时间超过该天数的旧文直接跳过（防 RSS 源回吐历史文章）
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "2"))

# 评分参与采集筛选（混合力度）：
# - 来源门控：均分 < SOURCE_MIN_TRUST 且评分 >= SOURCE_MIN_RATINGS 条的源，整轮跳过
# - 内容偏好：正偏好词（高分标题）命中加权；反偏好词（低分标题）命中且无正偏好则硬删
# - 探索保底：每源名额中保 EXPLORATION_BUDGET 比例给非偏好文章（防越筛越窄）
SOURCE_MIN_TRUST = float(os.getenv("SOURCE_MIN_TRUST", "4.0"))
SOURCE_MIN_RATINGS = int(os.getenv("SOURCE_MIN_RATINGS", "3"))

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





